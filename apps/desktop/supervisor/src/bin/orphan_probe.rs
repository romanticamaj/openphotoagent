//! Test helper for DESK-006: starts `sleep 600` the way the shell starts sidecars, prints its pid, then waits
//! to be killed. The integration test kills this process with SIGKILL and expects the child to go too.
use std::process::Command;

fn main() {
    let mut cmd = Command::new("sleep");
    cmd.arg("600").stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null());
    let s = opagent_supervisor::children::spawn_supervised(cmd).expect("spawn");
    println!("{}", s.pid());
    loop {
        std::thread::park();
    }
}
