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
    let by_env = env(DISABLE_ENV)
        .map(|v| !matches!(v.trim(), "" | "0" | "false" | "no"))
        .unwrap_or(false);
    by_env || setting_disabled
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Install {
    /// the user pressed "update and restart" and nothing is running
    Now,
    /// announce only (tray + settings badge); the user can install once jobs finish
    AnnounceOnly,
}

pub fn install_decision(running_jobs: usize, user_requested: bool) -> Install {
    if user_requested && running_jobs == 0 {
        Install::Now
    } else {
        Install::AnnounceOnly
    }
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
        Self {
            fetch,
            channel,
            disabled,
            last_check: None,
        }
    }

    /// Called on start-up and on a timer tick; `now` is a monotonic offset. Returns an available version.
    pub fn tick(&mut self, now: Duration) -> Option<String> {
        if self.disabled {
            return None;
        }
        if let Some(t) = self.last_check {
            if now.saturating_sub(t) < CHECK_EVERY {
                return None;
            }
        }
        self.last_check = Some(now);
        self.fetch.check(endpoint(self.channel))
    }
}

// ---------------------------------------------------------------- channels (UPD-010)

/// A release version: `MAJOR.MINOR.PATCH[-pre]`. Pre-releases sort before their release (semver §11).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Version {
    core: (u64, u64, u64),
    pre: Option<String>,
}

impl Version {
    pub fn parse(s: &str) -> Option<Version> {
        let s = s.trim().trim_start_matches('v');
        let (core, pre) = match s.split_once('-') {
            Some((c, p)) if !p.is_empty() => (c, Some(p.to_string())),
            Some(_) => return None,
            None => (s, None),
        };
        let mut it = core.split('.').map(|p| p.parse::<u64>().ok());
        let v = (it.next()??, it.next()??, it.next()??);
        if it.next().is_some() {
            return None;
        }
        Some(Version { core: v, pre })
    }

    pub fn is_prerelease(&self) -> bool {
        self.pre.is_some()
    }
}

impl PartialOrd for Version {
    fn partial_cmp(&self, other: &Self) -> Option<std::cmp::Ordering> {
        Some(self.cmp(other))
    }
}

impl Ord for Version {
    fn cmp(&self, other: &Self) -> std::cmp::Ordering {
        use std::cmp::Ordering::*;
        match self.core.cmp(&other.core) {
            Equal => match (&self.pre, &other.pre) {
                (None, None) => Equal,
                (None, Some(_)) => Greater,
                (Some(_), None) => Less,
                (Some(a), Some(b)) => cmp_pre(a, b),
            },
            o => o,
        }
    }
}

fn cmp_pre(a: &str, b: &str) -> std::cmp::Ordering {
    let (pa, pb): (Vec<&str>, Vec<&str>) = (a.split('.').collect(), b.split('.').collect());
    for (x, y) in pa.iter().zip(pb.iter()) {
        let o = match (x.parse::<u64>(), y.parse::<u64>()) {
            (Ok(m), Ok(n)) => m.cmp(&n),
            (Ok(_), Err(_)) => std::cmp::Ordering::Less,
            (Err(_), Ok(_)) => std::cmp::Ordering::Greater,
            _ => x.cmp(y),
        };
        if o != std::cmp::Ordering::Equal {
            return o;
        }
    }
    pa.len().cmp(&pb.len())
}

/// One entry of an update manifest as the shell sees it, before installing.
pub struct Offer<'a> {
    pub version: &'a str,
    pub signature: Option<&'a str>,
}

/// Which update to offer: the newest signed version the channel allows that is strictly newer than what runs.
/// Stable never offers a pre-release; leaving beta for stable never downgrades — it waits for the next stable.
pub fn choose<'a>(
    channel: Channel,
    current: &str,
    offers: &[Offer<'a>],
    verify: &dyn Fn(&Offer<'a>) -> bool,
) -> Option<&'a str> {
    let cur = Version::parse(current)?;
    offers
        .iter()
        .filter(|o| o.signature.is_some() && verify(o)) // an unsigned or badly signed entry is never offered
        .filter_map(|o| Version::parse(o.version).map(|v| (v, o.version)))
        .filter(|(v, _)| channel == Channel::Beta || !v.is_prerelease())
        .filter(|(v, _)| *v > cur)
        .max_by(|a, b| a.0.cmp(&b.0))
        .map(|(_, s)| s)
}

#[cfg(test)]
mod channel_tests {
    use super::*;

    fn ok(_: &Offer) -> bool {
        true
    }

    fn signed(v: &str) -> Offer<'_> {
        Offer {
            version: v,
            signature: Some("sig"),
        }
    }

    #[test]
    fn upd_010_stable_offers_only_releases() {
        let m = [signed("1.0.0"), signed("1.1.0-beta.1")];
        assert_eq!(choose(Channel::Stable, "0.5.0", &m, &ok), Some("1.0.0"));
        assert_eq!(choose(Channel::Stable, "1.0.0", &m, &ok), None);
        assert_eq!(
            choose(Channel::Beta, "1.0.0", &m, &ok),
            Some("1.1.0-beta.1")
        );
        assert_eq!(
            choose(Channel::Beta, "0.5.0", &m, &ok),
            Some("1.1.0-beta.1")
        );
    }

    #[test]
    fn upd_010_leaving_beta_never_downgrades() {
        let m = [signed("1.0.0"), signed("1.1.0-beta.2")];
        // running a beta, switched to stable: 1.0.0 is older than 1.1.0-beta.1, so nothing until 1.1.0
        assert_eq!(choose(Channel::Stable, "1.1.0-beta.1", &m, &ok), None);
        let later = [signed("1.0.0"), signed("1.1.0")];
        assert_eq!(
            choose(Channel::Stable, "1.1.0-beta.1", &later, &ok),
            Some("1.1.0")
        );
        assert!(Version::parse("1.1.0-beta.10").unwrap() > Version::parse("1.1.0-beta.2").unwrap());
        assert!(Version::parse("1.1.0-rc.1").unwrap() > Version::parse("1.1.0-beta.9").unwrap());
        assert!(Version::parse("1.0.0").unwrap() > Version::parse("1.0.0-rc.1").unwrap());
    }

    #[test]
    fn upd_010_bad_signature_is_never_offered() {
        let m = [
            Offer {
                version: "1.2.0",
                signature: Some("forged"),
            },
            Offer {
                version: "1.1.0",
                signature: None,
            },
            signed("1.0.1"),
        ];
        let verify = |o: &Offer| o.signature == Some("sig");
        assert_eq!(choose(Channel::Stable, "1.0.0", &m, &verify), Some("1.0.1"));
        assert_eq!(choose(Channel::Stable, "garbage", &m, &verify), None);
        assert_eq!(Version::parse("1.2"), None);
        assert_eq!(Version::parse("1.2.3-"), None);
    }
}
