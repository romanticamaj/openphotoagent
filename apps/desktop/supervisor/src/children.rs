//! Spawning sidecars so they never outlive the shell (08 §1.1 orphan protection, DESK-006):
//! * Linux: `prctl(PR_SET_PDEATHSIG, SIGKILL)` in the child before exec;
//! * macOS: the child keeps our stdin pipe and exits on EOF (the Python sidecars watch it);
//! * Windows: every child joins one Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`.
//! The shell keeps the write end of each child's stdin open for as long as it lives.

use std::io::{self, Write};
use std::process::{Child, ChildStdin, Command, Stdio};

pub struct Supervised {
    pub child: Child,
    /// held open: when the shell dies the OS closes it and the child sees EOF
    pub stdin: Option<ChildStdin>,
}

impl Supervised {
    pub fn pid(&self) -> u32 {
        self.child.id()
    }

    /// Send one line (the launch token) over stdin — never argv or the environment (DESK-012).
    pub fn send_line(&mut self, line: &str) -> io::Result<()> {
        if let Some(s) = self.stdin.as_mut() {
            s.write_all(line.as_bytes())?;
            s.write_all(b"\n")?;
            s.flush()?;
        }
        Ok(())
    }
}

pub fn spawn_supervised(mut cmd: Command) -> io::Result<Supervised> {
    cmd.stdin(Stdio::piped());
    #[cfg(any())]
    {
        use std::os::unix::process::CommandExt;
        let parent = unsafe { libc::getpid() };
        unsafe {
            cmd.pre_exec(move || {
                if libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGKILL, 0, 0, 0) != 0 {
                    return Err(io::Error::last_os_error());
                }
                if libc::getppid() != parent {
                    // the shell died between fork and prctl: do not start an orphan
                    libc::_exit(0);
                }
                Ok(())
            });
        }
    }
    let mut child = cmd.spawn()?;
    #[cfg(windows)]
    job::assign(&child)?;
    let stdin = child.stdin.take();
    Ok(Supervised { child, stdin })
}

#[cfg(windows)]
mod job {
    use std::io;
    use std::os::windows::io::AsRawHandle;
    use std::sync::OnceLock;
    use windows_sys::Win32::Foundation::HANDLE;
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation, SetInformationJobObject,
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };

    struct Job(HANDLE);
    unsafe impl Send for Job {}
    unsafe impl Sync for Job {}

    fn job() -> io::Result<&'static Job> {
        static JOB: OnceLock<Job> = OnceLock::new();
        if let Some(j) = JOB.get() {
            return Ok(j);
        }
        unsafe {
            let h = CreateJobObjectW(std::ptr::null(), std::ptr::null());
            if h.is_null() {
                return Err(io::Error::last_os_error());
            }
            let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            let ok = SetInformationJobObject(
                h,
                JobObjectExtendedLimitInformation,
                &info as *const _ as *const core::ffi::c_void,
                std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            );
            if ok == 0 {
                return Err(io::Error::last_os_error());
            }
            // the handle is never closed: the OS closes it when the shell exits, which kills the job
            Ok(JOB.get_or_init(|| Job(h)))
        }
    }

    pub fn assign(child: &std::process::Child) -> io::Result<()> {
        let j = job()?;
        let ok = unsafe { AssignProcessToJobObject(j.0, child.as_raw_handle() as HANDLE) };
        if ok == 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(())
    }
}
