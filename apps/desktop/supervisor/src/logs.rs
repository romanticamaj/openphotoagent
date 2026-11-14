//! Where a sidecar's stdout/stderr go (08 §1.5). A failed start has nowhere else to speak: the shell has no console.
//! The python processes keep their own JSON log (`api.log`); this is what they print before that exists, or when they
//! crash, so it is a separate file (`api.stderr.log`): Windows cannot rotate a file another process holds open.

use std::fs::{self, File, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};

pub const MAX_BYTES: u64 = 10 * 1024 * 1024;
pub const KEEP: usize = 5;

fn nth(path: &Path, n: usize) -> PathBuf {
    let mut name = path.as_os_str().to_owned();
    name.push(format!(".{n}"));
    PathBuf::from(name)
}

/// Rotates `file` at start-up when it has grown past `MAX_BYTES`, then opens it for appending.
pub fn open_rotated(dir: &Path, file: &str, max_bytes: u64) -> io::Result<File> {
    fs::create_dir_all(dir)?;
    let path = dir.join(file);
    if fs::metadata(&path).map(|m| m.len()).unwrap_or(0) >= max_bytes {
        let _ = fs::remove_file(nth(&path, KEEP));
        for n in (1..KEEP).rev() {
            let _ = fs::rename(nth(&path, n), nth(&path, n + 1));
        }
        let _ = fs::rename(&path, nth(&path, 1));
    }
    OpenOptions::new().create(true).append(true).open(path)
}

/// One JSON line, like the python processes (08 §1.5).
pub fn line(file: &mut File, ts: u64, level: &str, msg: &str) -> io::Result<()> {
    let row = serde_json::json!({"ts": ts, "level": level, "proc": "shell", "msg": msg});
    writeln!(file, "{row}")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("opa-logs-{}-{name}", std::process::id()));
        let _ = fs::remove_dir_all(&d);
        d
    }

    #[test]
    fn desk_046_log_rotates_at_start_and_keeps_five() {
        let d = tmp("rot");
        for i in 0..8 {
            let mut f = open_rotated(&d, "api.stderr.log", 4).unwrap();
            write!(f, "run{i}").unwrap();
        }
        assert_eq!(
            fs::read_to_string(d.join("api.stderr.log")).unwrap(),
            "run7"
        );
        assert_eq!(
            fs::read_to_string(d.join("api.stderr.log.1")).unwrap(),
            "run6"
        );
        assert!(d.join("api.stderr.log.5").exists());
        assert!(!d.join("api.stderr.log.6").exists());
    }

    #[test]
    fn desk_046_shell_lines_are_json() {
        let d = tmp("json");
        let mut f = open_rotated(&d, "shell.log", MAX_BYTES).unwrap();
        line(&mut f, 7, "ERROR", "boom \"quoted\"").unwrap();
        let text = fs::read_to_string(d.join("shell.log")).unwrap();
        let v: serde_json::Value = serde_json::from_str(text.trim()).unwrap();
        assert_eq!(
            (v["proc"].as_str(), v["msg"].as_str()),
            (Some("shell"), Some("boom \"quoted\""))
        );
    }
}
