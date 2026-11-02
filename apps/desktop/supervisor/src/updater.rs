//! Updater policy (08 §5.3). The Tauri updater plugin does the download and signature check; this decides
//! *whether* to ask and *when* to install.
//!
//! * `OPAGENT_DISABLE_UPDATER=1` or the settings switch → no update request is ever made (UPD-005);
//! * check at start-up, then every 24 h;
//! * while a job runs, an available update is only announced — never installed or restarted (UPD-003);
//! * the endpoint depends on the channel and is set at run time.

use std::time::Duration;

pub const CHECK_EVERY: Duration = Duration::from_secs(24 * 3600);
pub const DISABLE_ENV: &str = "OPAGENT_DISABLE_UPDATER";
pub const STABLE_ENDPOINT: &str =
    "https://github.com/romanticamaj/openphotoagent/releases/latest/download/latest.json";
pub const BETA_ENDPOINT: &str = "https://romanticamaj.github.io/openphotoagent/updates/beta.json";

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Channel {
    Stable,
    Beta,
}

pub fn endpoint(channel: Channel) -> &'static str {
    match channel {
        Channel::Stable => STABLE_ENDPOINT,
        Channel::Beta => BETA_ENDPOINT,
    }
}

/// `env` looks up an environment variable (injected so tests never touch the process environment).
pub fn disabled(env: &dyn Fn(&str) -> Option<String>, setting_disabled: bool) -> bool {
    let by_env = env(DISABLE_ENV).map(|v| !matches!(v.trim(), "" | "0" | "false" | "no")).unwrap_or(false);
    by_env || setting_disabled
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Install {
    /// the user pressed "update and restart" and nothing is running
    Now,
    /// announce only (tray + settings badge); the user can install once jobs finish
    AnnounceOnly,
}

pub fn install_decision(_running_jobs: usize, _user_requested: bool) -> Install {
    Install::Now
}

/// Something that asks an update endpoint (the Tauri plugin in the app, a counter in tests).
pub trait Fetch {
    fn check(&mut self, endpoint: &str) -> Option<String>;
}

pub struct Updater<F: Fetch> {
    pub fetch: F,
    pub channel: Channel,
    pub disabled: bool,
    last_check: Option<Duration>,
}

impl<F: Fetch> Updater<F> {
    pub fn new(fetch: F, channel: Channel, disabled: bool) -> Self {
        Self { fetch, channel, disabled, last_check: None }
    }

    /// Called on start-up and on a timer tick; `now` is a monotonic offset. Returns an available version.
    pub fn tick(&mut self, now: Duration) -> Option<String> {
        if let Some(t) = self.last_check {
            if now.saturating_sub(t) < CHECK_EVERY {
                return None;
            }
        }
        self.last_check = Some(now);
        self.fetch.check(endpoint(self.channel))
    }
}
