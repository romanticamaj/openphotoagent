//! What a sidecar's exit code means to the shell. The codes are set by `opagent api` (apps/cli).

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ExitClass {
    /// clean shutdown we asked for
    Stopped,
    /// another OpenPhotoAgent already serves this data folder: focus it, do not restart (DESK-010)
    AlreadyRunning,
    /// the database was written by a newer app: never restart; show "restore a backup" (PG-008, UPD-004)
    DatabaseTooNew,
    /// LAN access is on but no password is set: settings page, no restart (DESK-015)
    LanPasswordRequired,
    /// anything else: a crash, handled by the restart policy
    Crashed,
}

pub const EXIT_ALREADY_RUNNING: i32 = 3;
pub const EXIT_DB_TOO_NEW: i32 = 4;
pub const EXIT_LAN_PASSWORD_REQUIRED: i32 = 5;

/// `code` is `None` when the process was killed by a signal.
pub fn classify(code: Option<i32>, requested_stop: bool) -> ExitClass {
    match (code, requested_stop) {
        (_, true) => ExitClass::Stopped,
        (Some(EXIT_ALREADY_RUNNING), _) => ExitClass::AlreadyRunning,
        (Some(EXIT_DB_TOO_NEW), _) => ExitClass::DatabaseTooNew,
        (Some(EXIT_LAN_PASSWORD_REQUIRED), _) => ExitClass::LanPasswordRequired,
        _ => ExitClass::Crashed,
    }
}

impl ExitClass {
    /// Only crashes go through the restart policy; the other classes are answers, not failures.
    pub fn restartable(self) -> bool {
        matches!(self, ExitClass::Crashed)
    }
}
