//! What happens when the app is closed and opened again straight away.
//!
//! Closing stops the backend gracefully (jobs are checkpointed), which can take a while. The old process used to do that on the thread that
//! also handles "somebody started a second copy", so a second launch during the wait was swallowed and nothing appeared. Now the
//! window disappears at once, the shutdown runs on its own thread, and a second launch during it is remembered: when the shutdown is
//! finished this copy starts a fresh one and exits, so opening the app again always ends with the app open.

use std::sync::atomic::{AtomicU8, Ordering};

const RUNNING: u8 = 0;
const SHUTTING_DOWN: u8 = 1;
const RELAUNCH: u8 = 2; // shutting down, and somebody asked for the app meanwhile
const DONE: u8 = 3;

#[derive(Debug, PartialEq, Eq)]
pub enum ExitDecision {
    /// First request to exit: stop the backend (off the main thread) and keep the process until that is done.
    BeginShutdown,
    /// A shutdown is already under way: keep waiting.
    Wait,
    /// The shutdown has finished: let the process go.
    Exit,
}

#[derive(Debug, PartialEq, Eq)]
pub enum SecondLaunch {
    /// The app is running: bring its window forward.
    ShowWindow,
    /// The app is closing: start again once it has finished.
    RelaunchAfterShutdown,
}

pub struct Lifecycle(AtomicU8);

impl Lifecycle {
    pub const fn new() -> Lifecycle {
        Lifecycle(AtomicU8::new(RUNNING))
    }

    pub fn exit_requested(&self) -> ExitDecision {
        match self.0.compare_exchange(RUNNING, SHUTTING_DOWN, Ordering::SeqCst, Ordering::SeqCst) {
            Ok(_) => ExitDecision::BeginShutdown,
            Err(DONE) => ExitDecision::Exit,
            Err(_) => ExitDecision::Wait,
        }
    }

    pub fn second_launch(&self) -> SecondLaunch {
        loop {
            let now = self.0.load(Ordering::SeqCst);
            match now {
                RUNNING | DONE => return SecondLaunch::ShowWindow,
                RELAUNCH => return SecondLaunch::RelaunchAfterShutdown,
                _ => {
                    if self.0.compare_exchange(SHUTTING_DOWN, RELAUNCH, Ordering::SeqCst, Ordering::SeqCst).is_ok() {
                        return SecondLaunch::RelaunchAfterShutdown;
                    }
                }
            }
        }
    }

    /// The backend has stopped. Returns whether a fresh copy of the app should be started.
    pub fn shutdown_finished(&self) -> bool {
        self.0.swap(DONE, Ordering::SeqCst) == RELAUNCH
    }
}

impl Default for Lifecycle {
    fn default() -> Self {
        Lifecycle::new()
    }
}

/// Set on the copy started to replace one that was closing: the old process has just handed over and is exiting, and the single-instance
/// lock is only released when it has, so the new one waits a moment before it takes the lock.
pub const RELAUNCH_ENV: &str = "ARCHIVE_X_RELAUNCH";

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn closing_once_begins_a_shutdown_and_asking_again_just_waits() {
        let l = Lifecycle::new();
        assert_eq!(l.exit_requested(), ExitDecision::BeginShutdown);
        assert_eq!(l.exit_requested(), ExitDecision::Wait);
        assert!(!l.shutdown_finished());
        assert_eq!(l.exit_requested(), ExitDecision::Exit, "once finished the process may go");
    }

    #[test]
    fn a_second_launch_while_running_shows_the_window() {
        let l = Lifecycle::new();
        assert_eq!(l.second_launch(), SecondLaunch::ShowWindow);
    }

    #[test]
    fn a_second_launch_during_shutdown_is_remembered_and_starts_a_new_copy_afterwards() {
        let l = Lifecycle::new();
        assert_eq!(l.exit_requested(), ExitDecision::BeginShutdown);
        assert_eq!(l.second_launch(), SecondLaunch::RelaunchAfterShutdown);
        assert_eq!(l.second_launch(), SecondLaunch::RelaunchAfterShutdown, "however many times it is tried");
        assert_eq!(l.exit_requested(), ExitDecision::Wait);
        assert!(l.shutdown_finished(), "the new copy is started");
        assert_eq!(l.exit_requested(), ExitDecision::Exit);
    }

    #[test]
    fn nobody_asking_means_no_new_copy() {
        let l = Lifecycle::new();
        l.exit_requested();
        assert!(!l.shutdown_finished());
    }
}
