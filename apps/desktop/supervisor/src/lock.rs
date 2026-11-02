//! `opagent.lock` in the data dir (08 §1.3): an OS file lock, so two installs (AppImage + deb, or two copies
//! of the app) never open the same data at once (DESK-008). The lock dies with the process — no stale files.

use std::fs::{File, OpenOptions, TryLockError};
use std::io;
use std::path::{Path, PathBuf};

pub const LOCK_FILE: &str = "opagent.lock";

#[derive(Debug)]
pub enum LockError {
    /// another running instance holds the data directory
    InUse(PathBuf),
    Io(io::Error),
}

/// Held for the lifetime of the shell; dropping it releases the lock.
#[derive(Debug)]
pub struct DataDirLock {
    _file: File,
    pub path: PathBuf,
}

impl DataDirLock {
    pub fn acquire(data_dir: &Path) -> Result<Self, LockError> {
        std::fs::create_dir_all(data_dir).map_err(LockError::Io)?;
        let path = data_dir.join(LOCK_FILE);
        let file = OpenOptions::new()
            .create(true)
            .truncate(false)
            .read(true)
            .write(true)
            .open(&path)
            .map_err(LockError::Io)?;
        match file.try_lock() {
            Ok(()) => Ok(Self { _file: file, path }),
            Err(TryLockError::WouldBlock) => Err(LockError::InUse(path)),
            Err(TryLockError::Error(e)) => Err(LockError::Io(e)),
        }
    }
}
