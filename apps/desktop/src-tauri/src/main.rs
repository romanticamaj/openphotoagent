//! OpenPhotoAgent desktop shell (docs/08 §1). Thin glue: decisions live in `opagent_supervisor` (tested there).
//!
//! Boot: lock the data dir → splash → `opagent pg-prepare` → run postgres ourselves → api (token on stdin) →
//! wait for run/api.json → worker (token on stdin). Exit: worker → api → postgres, via RunEvent::ExitRequested
//! (never std::process::exit, which skips cleanup).
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use opagent_supervisor::backoff::{Decision, Policy, RestartTracker};
use opagent_supervisor::children::{hide_console, spawn_supervised, Supervised};
use opagent_supervisor::exit::{classify, ExitClass};
use opagent_supervisor::lock::{DataDirLock, LockError};
use opagent_supervisor::logs;
use opagent_supervisor::paths::{self, Os};
use opagent_supervisor::remote;
use opagent_supervisor::roots;
use opagent_supervisor::updater;
use serde::Serialize;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use tauri::{Emitter, Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};

#[derive(Clone, Serialize)]
struct BackendInfo {
    base_url: String,
    token: String,
}

/// What the SPA shows before the API answers (DESK-001), and instead of it when start-up failed.
#[derive(Clone, Default, Serialize)]
struct BootStatus {
    fatal: Option<String>,
    detail: Option<String>,
    reconnecting: Option<String>,
    logs: String,
}

struct Shell {
    home: PathBuf,
    python: PathBuf,
    pg_bin: PathBuf,
    runtime_dir: PathBuf,
    pg_stop: Mutex<Option<(String, String)>>,
    status: Mutex<BootStatus>,
    token: String,
    started: Instant,
    children: Mutex<Vec<(&'static str, Supervised)>>,
    backend: Mutex<Option<BackendInfo>>,
    stopping: Mutex<bool>,
    _lock: DataDirLock,
}

fn launch_token() -> String {
    let mut b = [0u8; 32]; // 256-bit, regenerated on every start (08 §1.3)
    getrandom::fill(&mut b).expect("OS randomness");
    b.iter().map(|x| format!("{x:02x}")).collect()
}

fn data_home() -> PathBuf {
    let user = dirs::home_dir().unwrap_or_else(std::env::temp_dir);
    paths::data_home(
        Os::current(),
        &|k: &str| std::env::var(k).ok(),
        &user.to_string_lossy(),
    )
}

fn python_in(resources: &Path) -> PathBuf {
    if cfg!(windows) {
        resources.join("python").join("python.exe")
    } else {
        resources.join("python").join("bin").join("python3")
    }
}

impl Shell {
    fn opagent(&self, args: &[&str]) -> Command {
        let mut c = Command::new(&self.python);
        c.arg("-I").arg("-m").arg("opagent_cli").args(args);
        c.env_remove("PYTHONPATH").env_remove("PYTHONHOME"); // DESK-027
        self.sidecar_env(&mut c);
        c
    }

    /// Every child, postgres included: pg-run needs the pack too when the app is elevated.
    fn sidecar_env(&self, c: &mut Command) {
        c.env("OPAGENT_PG_BIN", &self.pg_bin); // the bundled pack, also for pg_dump in the api
        c.env("OPAGENT_RUNTIME_DIR", &self.runtime_dir); // llama-server inside the app (08 §4.1)
        c.env_remove("PGPASSWORD"); // outranks the passfile: a value from the person's shell broke every login
        hide_console(c);
    }

    /// Sends a sidecar's stdout/stderr to `logs/<name>`: it has no console, and a failed start has nothing else.
    fn logged(&self, mut c: Command, file: &str) -> Result<Command, String> {
        let dir = self.home.join("logs");
        let log = logs::open_rotated(&dir, file, logs::MAX_BYTES).map_err(|e| e.to_string())?;
        c.stdout(Stdio::from(log.try_clone().map_err(|e| e.to_string())?));
        c.stderr(Stdio::from(log));
        Ok(c)
    }

    fn log(&self, level: &str, msg: &str) {
        let dir = self.home.join("logs");
        if let Ok(mut f) = logs::open_rotated(&dir, "shell.log", logs::MAX_BYTES) {
            let ts = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_secs())
                .unwrap_or(0);
            let _ = logs::line(&mut f, ts, level, msg);
        }
    }

    fn fatal(&self, app: &tauri::AppHandle, code: &str, detail: &str) {
        self.log("ERROR", &format!("{code}: {detail}"));
        {
            let mut st = self.status.lock().unwrap();
            st.fatal = Some(code.to_string());
            st.detail = Some(detail.chars().take(4000).collect());
        }
        let _ = app.emit("shell://fatal", code);
    }

    fn start_postgres(&self) -> Result<String, String> {
        let pg_home = self.home.join("pg");
        let out = self
            .opagent(&["pg-prepare", "--home", pg_home.to_str().unwrap_or_default()])
            .stdin(Stdio::null())
            .output()
            .map_err(|e| e.to_string())?;
        if !out.status.success() {
            return Err(String::from_utf8_lossy(&out.stderr).into_owned());
        }
        let info: serde_json::Value =
            serde_json::from_slice(&out.stdout).map_err(|e| e.to_string())?;
        let argv: Vec<String> = info["argv"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|v| v.as_str().map(String::from))
            .collect();
        let (exe, rest) = argv.split_first().ok_or("empty postgres argv")?;
        if let (Some(ctl), Some(data)) = (info["pg_ctl"].as_str(), info["data"].as_str()) {
            *self.pg_stop.lock().unwrap() = Some((ctl.to_string(), data.to_string()));
        }
        let mut cmd = Command::new(exe);
        cmd.args(rest);
        self.sidecar_env(&mut cmd);
        let cmd = self.logged(cmd, "postgres.log")?;
        let child = spawn_supervised(cmd).map_err(|e| e.to_string())?;
        self.children.lock().unwrap().push(("postgres", child));
        Ok(info["dsn"].as_str().unwrap_or_default().to_string())
    }

    fn start_api(&self, dsn: &str) -> Result<(), String> {
        let home = self.home.to_str().unwrap_or_default().to_string();
        // a leftover from the last run names a port nobody may be listening on
        let _ = std::fs::remove_file(self.home.join("run").join("api.json"));
        let cmd = self.opagent(&["api", "--dsn", dsn, "--token-stdin", "--home", &home]);
        let mut child =
            spawn_supervised(self.logged(cmd, "api.stderr.log")?).map_err(|e| e.to_string())?;
        child.send_line(&self.token).map_err(|e| e.to_string())?; // never argv / env (DESK-012)
        self.children.lock().unwrap().push(("api", child));
        Ok(())
    }

    fn wait_for_api(&self, timeout: Duration) -> Option<BackendInfo> {
        let run = self.home.join("run").join("api.json");
        let t0 = Instant::now();
        while t0.elapsed() < timeout {
            if let Ok(text) = std::fs::read_to_string(&run) {
                if let Ok(v) = serde_json::from_str::<serde_json::Value>(&text) {
                    if let Some(port) = v["port"].as_u64() {
                        return Some(BackendInfo {
                            base_url: format!("http://127.0.0.1:{port}"),
                            token: self.token.clone(),
                        });
                    }
                }
            }
            std::thread::sleep(Duration::from_millis(50));
        }
        None
    }

    fn roots_file(&self) -> PathBuf {
        self.home.join("config").join("roots.json")
    }

    fn start_worker(&self, base_url: &str) -> Result<(), String> {
        let wh = self.home.join("worker");
        let roots_file = self.roots_file();
        let models = self.home.join("models");
        let cmd = self.opagent(&[
            "worker",
            "--api",
            base_url,
            "--roots-file",
            roots_file.to_str().unwrap_or_default(),
            "--worker-id",
            "desktop",
            "--home",
            wh.to_str().unwrap_or_default(),
            "--token-stdin",
            "--backend",
            "auto",
            "--models-root",
            models.to_str().unwrap_or_default(),
        ]);
        let mut child =
            spawn_supervised(self.logged(cmd, "worker.stderr.log")?).map_err(|e| e.to_string())?;
        child.send_line(&self.token).map_err(|e| e.to_string())?;
        self.children.lock().unwrap().push(("worker", child));
        Ok(())
    }

    /// The worker reads its roots once, at start: a new folder takes a restart, which keeps its job leases safe.
    fn restart_worker(&self) -> Result<(), String> {
        let old = {
            let mut kids = self.children.lock().unwrap();
            kids.iter()
                .position(|(n, _)| *n == "worker")
                .map(|i| kids.remove(i))
        };
        if let Some((_, mut s)) = old {
            drop(s.stdin.take());
            let t0 = Instant::now();
            while t0.elapsed() < Duration::from_secs(10) {
                if let Ok(Some(_)) = s.child.try_wait() {
                    break;
                }
                std::thread::sleep(Duration::from_millis(50));
            }
            if s.child.try_wait().ok().flatten().is_none() {
                let _ = s.child.kill();
            }
        }
        let b = self
            .backend
            .lock()
            .unwrap()
            .clone()
            .ok_or("api not ready")?;
        self.start_worker(&b.base_url)
    }

    /// `pg_ctl stop`: postgres ignores stdin, so waiting for EOF only ended in a kill and crash recovery next start.
    fn stop_postgres(&self) {
        if let Some((ctl, data)) = self.pg_stop.lock().unwrap().clone() {
            let mut c = Command::new(ctl);
            c.args(["-D", &data, "-m", "fast", "-w", "stop"])
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null());
            hide_console(&mut c);
            let _ = c.status();
        }
    }

    /// Ordered shutdown (08 §1.1): worker first (it hands back its lease), then api, then postgres.
    fn shutdown(&self) {
        *self.stopping.lock().unwrap() = true;
        let mut kids = self.children.lock().unwrap();
        for name in ["worker", "api", "postgres"] {
            if name == "postgres" {
                self.stop_postgres();
            }
            for (n, s) in kids.iter_mut().filter(|(n, _)| *n == name) {
                drop(s.stdin.take()); // EOF: the sidecars stop themselves
                let t0 = Instant::now();
                while t0.elapsed() < Duration::from_secs(10) {
                    if let Ok(Some(_)) = s.child.try_wait() {
                        break;
                    }
                    std::thread::sleep(Duration::from_millis(50));
                }
                if s.child.try_wait().ok().flatten().is_none() {
                    eprintln!("{n} did not stop in time; killing");
                    let _ = s.child.kill();
                }
            }
        }
    }
}

/// Watches the children; restarts crashes by policy and turns the special exit codes into UI states.
fn supervise(app: tauri::AppHandle, shell: Arc<Shell>, dsn: String) {
    let mut trackers = [
        ("postgres", RestartTracker::new(Policy::POSTGRES)),
        ("api", RestartTracker::new(Policy::SIDECAR)),
        ("worker", RestartTracker::new(Policy::SIDECAR)),
    ];
    loop {
        std::thread::sleep(Duration::from_millis(500));
        if *shell.stopping.lock().unwrap() {
            return;
        }
        let exited: Vec<(&'static str, Option<i32>)> = {
            let mut kids = shell.children.lock().unwrap();
            let mut out = vec![];
            kids.retain_mut(|(n, s)| match s.child.try_wait() {
                Ok(Some(st)) => {
                    out.push((*n, st.code()));
                    false
                }
                _ => true,
            });
            out
        };
        for (name, code) in exited {
            match classify(code, false) {
                ExitClass::DatabaseTooNew => {
                    shell.fatal(&app, "DB_SCHEMA_TOO_NEW", name); // "restore a backup" page (UPD-004)
                    return;
                }
                ExitClass::LanPasswordRequired => {
                    shell.fatal(&app, "LAN_PASSWORD_REQUIRED", name);
                    return;
                }
                ExitClass::LayoutTooNew => {
                    shell.fatal(&app, "DATA_LAYOUT_TOO_NEW", name); // restore a backup / install newer (MIG-002)
                    return;
                }
                ExitClass::AlreadyRunning | ExitClass::Stopped => return,
                ExitClass::Crashed => {
                    let tracker = &mut trackers.iter_mut().find(|(n, _)| *n == name).unwrap().1;
                    match tracker.failed(shell.started.elapsed()) {
                        Decision::GiveUp => {
                            shell.fatal(
                                &app,
                                &format!("{name}_CRASHED"),
                                &format!("exit code {code:?}"),
                            );
                            return;
                        }
                        Decision::RestartAfter(d) => {
                            shell.log("WARN", &format!("{name} exited ({code:?}); restarting"));
                            shell.status.lock().unwrap().reconnecting = Some(name.to_string());
                            let _ = app.emit("shell://reconnecting", name); // keep screen state (DESK-002/003)
                            std::thread::sleep(d);
                            let r = match name {
                                "postgres" => shell.start_postgres().map(|_| ()),
                                "api" => shell.start_api(&dsn),
                                _ => shell
                                    .backend
                                    .lock()
                                    .unwrap()
                                    .clone()
                                    .ok_or("no api".into())
                                    .and_then(|b| shell.start_worker(&b.base_url)),
                            };
                            if let Err(e) = r {
                                shell.log("ERROR", &format!("restart {name} failed: {e}"));
                            }
                            shell.status.lock().unwrap().reconnecting = None;
                        }
                    }
                }
            }
        }
    }
}

/// The platform seam the SPA reads (apps/web/src/lib/platform.ts, 10 §5.4). Injected before any page script.
fn platform_script(thumbs: &Path) -> String {
    let dir = serde_json::to_string(&thumbs.to_string_lossy()).unwrap_or_else(|_| "\"\"".into());
    format!(
        r#"window.__OPA_PLATFORM__ = {{
  kind: "desktop",
  thumbsDir: {dir},
  assetUrl: (p) => window.__TAURI_INTERNALS__.convertFileSrc(p, "asset"),
  notify: (title, route) => {{
    if (document.hasFocus()) return; // the window is in front: the Activity Center already shows it
    window.__OPA_LAST_NOTIFY_ROUTE__ = route;
    window.__TAURI_INTERNALS__.invoke("plugin:notification|notify", {{ options: {{ title: "OpenPhotoAgent", body: title }} }});
  }},
  backend: async () => {{
    for (;;) {{
      const b = await window.__TAURI_INTERNALS__.invoke("backend_info");
      if (b) return {{ baseUrl: b.base_url, token: b.token }};
      await new Promise((r) => setTimeout(r, 100));
    }}
  }},
  status: () => window.__TAURI_INTERNALS__.invoke("shell_status"),
  addRoot: (mode) => window.__TAURI_INTERNALS__.invoke("add_root", {{ mode }}),
}};"#
    )
}

/// POST to the sidecar with the launch token (tray actions).
fn api_post(shell: &Shell, path: &str) -> Result<String, String> {
    let b = shell
        .backend
        .lock()
        .unwrap()
        .clone()
        .ok_or("api not ready")?;
    let mut resp = ureq::post(&format!("{}{}", b.base_url, path))
        .header("Authorization", &format!("Bearer {}", b.token))
        .send_empty()
        .map_err(|e| e.to_string())?;
    resp.body_mut().read_to_string().map_err(|e| e.to_string())
}

/// System tray (08 §6): pause / resume all heavy work (DESK-020), open in the browser via a one-time handoff code.
fn build_tray(app: &tauri::App, shell: Arc<Shell>) -> tauri::Result<()> {
    use tauri::menu::{Menu, MenuItem};
    use tauri::tray::TrayIconBuilder;
    let pause = MenuItem::with_id(app, "pause", "暫停全部 / Pause all", true, None::<&str>)?;
    let resume = MenuItem::with_id(app, "resume", "繼續全部 / Resume all", true, None::<&str>)?;
    let browser = MenuItem::with_id(
        app,
        "browser",
        "在瀏覽器開啟 / Open in browser",
        true,
        None::<&str>,
    )?;
    let quit = MenuItem::with_id(app, "quit", "結束 / Quit", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&pause, &resume, &browser, &quit])?;
    let mut tray = TrayIconBuilder::new().menu(&menu).tooltip("OpenPhotoAgent");
    if let Some(icon) = app.default_window_icon() {
        tray = tray.icon(icon.clone());
    }
    tray.on_menu_event(move |app, ev| match ev.id().as_ref() {
        "pause" => {
            let _ = api_post(&shell, "/api/v1/operations/pause-all");
        }
        "resume" => {
            let _ = api_post(&shell, "/api/v1/operations/resume-all");
        }
        "browser" => {
            if let Ok(body) = api_post(&shell, "/api/v1/auth/handoff") {
                let code = serde_json::from_str::<serde_json::Value>(&body)
                    .ok()
                    .and_then(|v| v["code"].as_str().map(String::from));
                if let (Some(code), Some(b)) = (code, shell.backend.lock().unwrap().clone()) {
                    use tauri_plugin_opener::OpenerExt;
                    let _ = app.opener().open_url(
                        format!("{}/auth/handoff?code={code}", b.base_url),
                        None::<&str>,
                    );
                }
            }
        }
        "quit" => app.exit(0), // goes through RunEvent::ExitRequested → ordered shutdown
        _ => {}
    })
    .build(app)?;
    Ok(())
}

#[tauri::command]
fn backend_info(shell: tauri::State<'_, Arc<Shell>>) -> Option<BackendInfo> {
    shell.backend.lock().unwrap().clone()
}

#[tauri::command]
fn shell_status(shell: tauri::State<'_, Arc<Shell>>) -> BootStatus {
    shell.status.lock().unwrap().clone()
}

/// The only way a folder becomes a root on the desktop (invariant 2, C-09): the person picks it in the OS's own
/// dialog, so a page cannot name a path. `None` when they cancel.
#[tauri::command(async)]
fn add_root(
    app: tauri::AppHandle,
    shell: tauri::State<'_, Arc<Shell>>,
    mode: String,
) -> Result<Option<String>, String> {
    use tauri_plugin_dialog::DialogExt;
    if mode != "ro" && mode != "rw" {
        return Err(format!("bad mode {mode}"));
    }
    let Some(picked) = app.dialog().file().blocking_pick_folder() else {
        return Ok(None);
    };
    let path = picked.into_path().map_err(|e| e.to_string())?;
    let file = shell.roots_file();
    let mut list = roots::load(&file);
    let alias = roots::add(&mut list, &path, &mode);
    roots::save(&file, &list).map_err(|e| e.to_string())?;
    shell.log("INFO", &format!("root {alias} added ({mode})"));
    shell.restart_worker()?;
    Ok(Some(alias))
}

fn main() {
    let home = data_home();
    let _ = std::fs::create_dir_all(&home);
    let lock = match DataDirLock::acquire(&home) {
        Ok(l) => l,
        Err(LockError::InUse(p)) => {
            eprintln!(
                "another OpenPhotoAgent uses this data folder ({})",
                p.display()
            ); // DESK-008
            std::process::exit(3);
        }
        Err(LockError::Io(e)) => {
            eprintln!("cannot lock the data folder: {e}");
            std::process::exit(1);
        }
    };
    let env = |k: &str| std::env::var(k).ok();
    let updates_off = updater::disabled(&env, false);
    let mut builder = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.set_focus(); // DESK-007: bring the existing window forward
            }
        }))
        .plugin(tauri_plugin_deep_link::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init());
    if !updates_off {
        builder = builder.plugin(tauri_plugin_updater::Builder::new().build()); // UPD-005: not even registered
    }
    let app = builder
        .invoke_handler(tauri::generate_handler![
            backend_info,
            shell_status,
            add_root
        ])
        .setup(move |app| {
            let resources = paths::plain(&app.path().resource_dir()?);
            let shell = Arc::new(Shell {
                home: home.clone(),
                python: python_in(&resources),
                pg_bin: resources.join("pg").join("bin"),
                runtime_dir: resources.join("llama"),
                pg_stop: Mutex::new(None),
                status: Mutex::new(BootStatus {
                    logs: home.join("logs").to_string_lossy().to_string(),
                    ..BootStatus::default()
                }),
                token: launch_token(),
                started: Instant::now(),
                children: Mutex::new(vec![]),
                backend: Mutex::new(None),
                stopping: Mutex::new(false),
                _lock: lock,
            });
            app.manage(shell.clone());
            build_tray(app, shell.clone())?;
            // thumbnails are read straight from the cache; the scope is exactly that directory (+ roots later)
            let thumbs = home.join("worker").join("thumbs");
            let _ = std::fs::create_dir_all(&thumbs);
            app.asset_protocol_scope().allow_directory(&thumbs, true)?;
            // v0.5: client of a self-hosted control plane → no local services, the window opens the CP itself
            let conn = std::fs::read_to_string(home.join("connection.json")).ok();
            let mode = remote::mode(&|k: &str| std::env::var(k).ok(), conn.as_deref())
                .unwrap_or(remote::Mode::Local);
            let start = remote::plan(&mode);
            if let Some(url) = start.window_url.clone() {
                let guard = mode.clone();
                WebviewWindowBuilder::new(
                    app,
                    "main",
                    WebviewUrl::External(url.parse().expect("checked origin")),
                )
                .title("OpenPhotoAgent")
                .inner_size(1280.0, 820.0)
                .min_inner_size(960.0, 600.0)
                .on_navigation(move |u| remote::may_navigate(&guard, u.as_str()))
                .build()?;
                return Ok(());
            }
            WebviewWindowBuilder::new(app, "main", WebviewUrl::default())
                .title("OpenPhotoAgent")
                .inner_size(1280.0, 820.0)
                .min_inner_size(960.0, 600.0)
                .initialization_script(&platform_script(&thumbs))
                .build()?;
            let pictures = dirs::picture_dir().unwrap_or_else(|| home.join("pictures"));
            if let Err(e) = roots::seed_output(&shell.roots_file(), &pictures) {
                shell.log("WARN", &format!("no output folder: {e}"));
            }
            let handle = app.handle().clone();
            std::thread::spawn(move || {
                let dsn = match shell.start_postgres() {
                    Ok(d) => d,
                    Err(e) => {
                        shell.fatal(&handle, "PG_START_FAILED", &e);
                        return;
                    }
                };
                if let Err(e) = shell.start_api(&dsn) {
                    shell.fatal(&handle, "API_START_FAILED", &e);
                    return;
                }
                match shell.wait_for_api(Duration::from_secs(60)) {
                    Some(info) => {
                        *shell.backend.lock().unwrap() = Some(info.clone());
                        let _ = handle.emit("shell://ready", info.clone());
                        if let Err(e) = shell.start_worker(&info.base_url) {
                            shell.log("ERROR", &format!("worker: {e}"));
                        }
                    }
                    None => {
                        shell.fatal(&handle, "API_START_TIMEOUT", "see api.stderr.log");
                        return;
                    }
                }
                supervise(handle, shell, dsn);
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("tauri app");
    app.run(|handle, event| {
        if let RunEvent::ExitRequested { .. } = event {
            if let Some(shell) = handle.try_state::<Arc<Shell>>() {
                shell.shutdown();
            }
        }
    });
}
