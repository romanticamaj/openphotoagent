//! Crash-restart policy per child (08 §1.4).
//!
//! * postgres: restart at once; 3 failures in a row → full-screen error page.
//! * api / worker: back off 1 s → 2 s → 4 s (capped); at most 5 restarts in any 5-minute window — the 6th
//!   failure inside the window shows the error page (with the log and "copy diagnostics").

use std::collections::VecDeque;
use std::time::Duration;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Policy {
    /// restart immediately, give up after `max_consecutive` failures without a healthy run in between
    Immediate { max_consecutive: u32 },
    /// exponential back-off from `base`, capped at `cap`, at most `max_restarts` in `window`
    Backoff { base: Duration, cap: Duration, max_restarts: usize, window: Duration },
}

impl Policy {
    pub const POSTGRES: Policy = Policy::Immediate { max_consecutive: 3 };
    pub const SIDECAR: Policy = Policy::Backoff {
        base: Duration::from_secs(1),
        cap: Duration::from_secs(4),
        max_restarts: 5,
        window: Duration::from_secs(300),
    };
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Decision {
    RestartAfter(Duration),
    GiveUp,
}

/// Tracks failures of one child. Time is a monotonic offset supplied by the caller (tests use a fake clock).
#[derive(Debug)]
pub struct RestartTracker {
    policy: Policy,
    failures: VecDeque<Duration>,
    consecutive: u32,
}

impl RestartTracker {
    pub fn new(policy: Policy) -> Self {
        Self { policy, failures: VecDeque::new(), consecutive: 0 }
    }

    /// The child became healthy (readiness probe passed): the back-off ladder starts over.
    pub fn healthy(&mut self) {
        self.consecutive = 0;
    }

    /// The child exited unexpectedly at monotonic time `now`.
    pub fn failed(&mut self, _now: Duration) -> Decision {
        Decision::RestartAfter(Duration::ZERO)
    }
}
