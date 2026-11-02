//! DESK-006: killing the shell with SIGKILL leaves no sidecar behind.
#![cfg(target_os = "linux")]

use std::io::{BufRead, BufReader};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

fn alive(pid: i32) -> bool {
    match std::fs::read_to_string(format!("/proc/{pid}/stat")) {
        Ok(s) => !s
            .rsplit(')')
            .next()
            .unwrap_or("")
            .trim_start()
            .starts_with('Z'),
        Err(_) => false,
    }
}

#[test]
fn desk_006_children_die_with_the_shell() {
    let mut probe = Command::new(env!("CARGO_BIN_EXE_opagent-orphan-probe"))
        .stdout(Stdio::piped())
        .spawn()
        .expect("probe");
    let mut line = String::new();
    BufReader::new(probe.stdout.take().unwrap())
        .read_line(&mut line)
        .unwrap();
    let child: i32 = line.trim().parse().unwrap();
    assert!(alive(child));
    probe.kill().unwrap(); // SIGKILL: no cleanup code runs in the shell
    probe.wait().unwrap();
    let t0 = Instant::now();
    while alive(child) && t0.elapsed() < Duration::from_secs(5) {
        std::thread::sleep(Duration::from_millis(50));
    }
    assert!(!alive(child), "sidecar {child} outlived the shell");
}
