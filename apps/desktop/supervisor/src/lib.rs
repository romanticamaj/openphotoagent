//! Testable core of the OpenPhotoAgent desktop shell (docs/08 §1). The Tauri app (`../src-tauri`) is a thin
//! layer over this crate: everything that decides *what* to do — restart or give up, which error page,
//! whether to check for updates — lives here, with an injected clock, so it runs under `cargo test` on any OS.

pub mod backoff;
pub mod children;
pub mod exit;
pub mod lock;
pub mod logs;
pub mod paths;
pub mod remote;
pub mod roots;
pub mod tc;
pub mod updater;

pub use backoff::{Decision, Policy, RestartTracker};
pub use exit::{classify, ExitClass};
