//! Desktop shell policy (08 §1.3–1.4, §5.3). Test names carry the 09a ids (tools/cargo_tc.py).

use opagent_supervisor::backoff::{Decision, Policy, RestartTracker};
use opagent_supervisor::exit::{classify, ExitClass};
use opagent_supervisor::lock::{DataDirLock, LockError};
use opagent_supervisor::updater::{self, Channel, Fetch, Install, Updater};
use std::time::Duration;

fn s(n: u64) -> Duration {
    Duration::from_secs(n)
}

#[test]
fn desk_003_api_backoff_1_2_4_and_sixth_failure_in_5_min_gives_up() {
    let mut t = RestartTracker::new(Policy::SIDECAR);
    let mut now = s(0);
    let mut delays = vec![];
    for _ in 0..5 {
        match t.failed(now) {
            Decision::RestartAfter(d) => {
                delays.push(d.as_secs());
                now += d + s(1);
            }
            Decision::GiveUp => panic!("gave up too early"),
        }
    }
    assert_eq!(delays, vec![1, 2, 4, 4, 4]);
    assert_eq!(t.failed(now), Decision::GiveUp); // 6th within 5 minutes → error page
}

#[test]
fn desk_003_failures_spread_beyond_the_window_keep_restarting() {
    let mut t = RestartTracker::new(Policy::SIDECAR);
    for i in 0..20 {
        t.healthy(); // ran fine for a while each time
        assert_eq!(
            t.failed(s(i * 120)),
            Decision::RestartAfter(s(1)),
            "failure {i}"
        );
    }
}

#[test]
fn desk_004_worker_restarts_with_the_same_policy() {
    let mut t = RestartTracker::new(Policy::SIDECAR);
    assert_eq!(t.failed(s(0)), Decision::RestartAfter(s(1)));
    assert_eq!(t.failed(s(2)), Decision::RestartAfter(s(2)));
    t.healthy();
    assert_eq!(t.failed(s(10)), Decision::RestartAfter(s(1))); // healthy again → ladder restarts
}

#[test]
fn desk_002_postgres_restarts_at_once_and_gives_up_after_three_in_a_row() {
    let mut t = RestartTracker::new(Policy::POSTGRES);
    assert_eq!(t.failed(s(0)), Decision::RestartAfter(Duration::ZERO));
    assert_eq!(t.failed(s(1)), Decision::RestartAfter(Duration::ZERO));
    assert_eq!(t.failed(s(2)), Decision::GiveUp);
    let mut t = RestartTracker::new(Policy::POSTGRES);
    for i in 0..10 {
        assert_eq!(t.failed(s(i)), Decision::RestartAfter(Duration::ZERO));
        t.healthy();
    }
}

#[test]
fn upd_004_newer_database_is_not_restarted_and_shows_restore() {
    assert_eq!(classify(Some(4), false), ExitClass::DatabaseTooNew);
    assert!(!ExitClass::DatabaseTooNew.restartable());
    assert_eq!(classify(Some(3), false), ExitClass::AlreadyRunning);
    assert_eq!(classify(Some(5), false), ExitClass::LanPasswordRequired);
    assert_eq!(classify(Some(1), false), ExitClass::Crashed);
    assert_eq!(classify(None, false), ExitClass::Crashed); // killed by a signal
    assert_eq!(classify(None, true), ExitClass::Stopped);
    assert!(ExitClass::Crashed.restartable());
}

#[test]
fn desk_008_second_instance_cannot_take_the_data_dir() {
    let dir = std::env::temp_dir().join(format!("opagent-lock-{}", std::process::id()));
    let first = DataDirLock::acquire(&dir).expect("first lock");
    match DataDirLock::acquire(&dir) {
        Err(LockError::InUse(p)) => assert_eq!(p, dir.join("opagent.lock")),
        other => panic!("expected InUse, got {other:?}"),
    }
    drop(first);
    let again = DataDirLock::acquire(&dir).expect("released with the holder");
    drop(again);
    let _ = std::fs::remove_dir_all(&dir);
}

struct Counter(usize, Option<String>);
impl Fetch for Counter {
    fn check(&mut self, _endpoint: &str) -> Option<String> {
        self.0 += 1;
        self.1.clone()
    }
}

#[test]
fn upd_005_disable_env_means_no_update_request_at_all() {
    let env = |k: &str| (k == "OPAGENT_DISABLE_UPDATER").then(|| "1".to_string());
    assert!(updater::disabled(&env, false));
    let mut u = Updater::new(
        Counter(0, Some("0.2.0".into())),
        Channel::Stable,
        updater::disabled(&env, false),
    );
    for h in 0..100 {
        assert_eq!(u.tick(s(h * 3600)), None);
    }
    assert_eq!(u.fetch.0, 0);
    let none = |_: &str| None;
    let zero = |_: &str| Some("0".to_string());
    assert!(!updater::disabled(&none, false) && !updater::disabled(&zero, false));
    assert!(updater::disabled(&none, true)); // the settings switch
}

#[test]
fn upd_005_enabled_checks_at_start_then_every_24h() {
    let mut u = Updater::new(Counter(0, None), Channel::Beta, false);
    for h in 0..49 {
        u.tick(s(h * 3600));
    }
    assert_eq!(u.fetch.0, 3); // t=0, 24 h, 48 h
    assert!(updater::endpoint(Channel::Beta).ends_with("/updates/beta.json"));
    assert!(updater::endpoint(Channel::Stable).ends_with("/releases/latest/download/latest.json"));
}

#[test]
fn upd_003_running_job_only_announces() {
    assert_eq!(updater::install_decision(1, true), Install::AnnounceOnly);
    assert_eq!(updater::install_decision(0, false), Install::AnnounceOnly);
    assert_eq!(updater::install_decision(0, true), Install::Now);
}
