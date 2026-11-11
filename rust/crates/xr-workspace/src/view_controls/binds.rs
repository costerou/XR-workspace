//! The run's Ctrl+Alt binds: what `managed.view_controls` builds around `client.install_view_binds` (tools/native_spike/
//! managed.py: `bind_args`, `view.session_mode`, `after_reload`, `menu_binds`, `typing_binds`, and in the `finally`
//! `control.stop_menu_binds()` and `client.remove_view_binds()`), with `ViewControl._reload_work`,
//! `_sync_menu_binds` / `_menu_bind_work` and `_typing_toggle`'s `_sync_typing_binds` / `_typing_bind_work`
//! (tools/native_spike/view.py).
//!
//! The Lua is `crate::lua::view`, sent by the `Client::install_view_binds` family. This module is the run's use of it:
//!
//! - the bind helper's command line ([`Binds::bind_args`]): the Python helper `tools/native_spike/xr_control.py` of the
//!   bundle this binary runs from, run by `/usr/bin/python3` (the Rust `xr-control` takes the same arguments, but the
//!   bind Lua's command line names an interpreter and a script, and the wire stays as it is);
//! - the session mode read at the start (`view.session_mode`, none on a Pro 2);
//! - the install with the key bind panel's keys, at the start;
//! - the work the view asks for off the loop: the reinstall after a config reload (typing mode kept, the presentation
//!   parked, the session or wide mode put back, 3D reasserted), the placement panel's and options menu's modal binds,
//!   and typing mode's swap of the bind table;
//! - the removal last, which never blocks recovery.
//!
//! # The bind table's lock ([`MenuLock`])
//!
//! The Python orders every write to the run's bind table with `ViewControl._menu_lock`: the reload, the panel's and
//! typing mode's swaps (view.py) and the key bind panel's reinstall and desktop read (keybinds.py) each take it, and
//! the flags of what is installed (`_typing_installed`, `_menu_binds_installed`, `_options_binds_installed`) live under
//! it. Here that is [`MenuLock`], shared by this module's worker, the key bind panel's worker
//! ([`super::keybinds::Keys`]) and the view:
//!
//! - It is a ticket lock. The loop takes a [`Ticket`] when it asks for the work ([`MenuLock::ticket`]), the worker
//!   waits for its turn ([`Ticket::wait`]) and the turn ends when the ticket is dropped. So the table is written in the
//!   order the loop asked, across both workers: a reload's reinstall always lands before the key bind panel's
//!   `after_reload` work that the view asks for right after it. A ticket dropped unused gives its turn up.
//! - It holds what is installed ([`Table`], the three flags) and what the view wants ([`Wanted`]: typing mode, the
//!   panel, the options menu's keyboard), which a worker reads when its turn comes, as the Python's work reads the
//!   view's state when it runs.
//!
//! The view (`view::ViewControl`) uses this module through [`Binds`] (the `binds` field of `ViewControls`):
//!
//! - `_sync_menu_binds(force)`: [`Binds::sync_menu`] with the panel's state;
//! - `_typing_toggle`'s `_sync_typing_binds()`: [`Binds::typing`];
//! - `on_events`'s reload: [`Binds::after_reload`] (with the 3D reassert, which runs after the mode is back), then
//!   `keys.after_reload(host)`;
//! - `options_binds` (managed.view_controls' last block, with `RuntimeText`): [`Binds::enable_options`];
//! - `join_focus` in tests: [`Binds::settle`].
//!
//! The view need never take the lock itself; [`Binds::lock`] is there for work of its own that must be ordered with
//! the table's writers (take a ticket on the loop, wait on it off the loop).
//!
//! Knowing differences: the Python's `_reload_work` is not joined by `stop_menu_binds`, so a reload in flight at the
//! run's end may still reinstall after the removal; here a reload after `stop_menu_binds` does nothing, and the stop
//! waits for one in flight. A Pro 2's reload (`display_restore`, the glasses' restore of `managed._restore_output`) is
//! not ported: its mode is not put back after a reload.

use std::collections::BTreeSet;
use std::io;
use std::path::PathBuf;
use std::sync::mpsc::{self, Sender};
use std::sync::{Arc, Condvar, Mutex, MutexGuard};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use super::Run;
use super::keybinds::Keys;
use super::socket::Server;
use crate::hyprland::{self, Client, Mode, Query};
use crate::lua::BindCommand;

/// The interpreter the bind Lua runs the helper with (`sys.executable` of an installed run).
pub const PYTHON: &str = "/usr/bin/python3";
/// The helper, from the bundle's root (`Path(view.__file__).with_name('xr_control.py')`).
pub const HELPER: &str = "tools/native_spike/xr_control.py";
/// `client.query('monitors', timeout=0.5)` in `after_reload`.
const RELOAD_READ: Duration = Duration::from_millis(500);
/// `stop_menu_binds(timeout=2.5)`.
const STOP_TIMEOUT: Duration = Duration::from_millis(2500);
/// `managed.after_reload`'s wide mode: `sbs.best_wide(now)` and `apply_wide_mode(output, wide)`, the default model.
const WIDE_MODEL: &str = "luma-ultra";

/// The key bind panel's keys for an install (`keys.bind_kwargs()`): the run binds and the send binds, `None` for the
/// default tables.
pub type KeyArgs = (Option<Vec<(String, String)>>, Option<Vec<(String, String)>>);

/// `run_3d.reassert()`, handed to a reload: it runs off the loop once the glasses' mode is back.
pub type Reassert = Box<dyn FnOnce() + Send>;

/// What the run's bind table holds now: `_typing_installed` (typing mode's table instead of the full set),
/// `_menu_binds_installed` (the placement panel's modal binds) and `_options_binds_installed` (the options menu's).
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Table {
    pub typing: bool,
    pub menu: bool,
    pub options: bool,
}

/// The view's state the bind work reads when it runs: `state.menu_open`, `menu_keys_off` (a press on a desktop gave
/// the keyboard back, the panel staying), `options.keyboard_owned` and `state.typing`.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Wanted {
    pub menu_open: bool,
    pub menu_keys_off: bool,
    pub options_owned: bool,
    pub typing: bool,
}

/// The panel's part of [`Wanted`], as [`Binds::sync_menu`] is given it.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct MenuState {
    pub menu_open: bool,
    pub menu_keys_off: bool,
    pub options_owned: bool,
}

/// `ViewControl._menu_lock` and the flags under it: see the module doc.
#[derive(Clone, Default)]
pub struct MenuLock {
    inner: Arc<Inner>,
}

#[derive(Default)]
struct Inner {
    state: Mutex<LockState>,
    turn: Condvar,
}

#[derive(Default)]
struct LockState {
    /// The next ticket's number, and the ticket whose turn it is.
    next: u64,
    serving: u64,
    /// Tickets after `serving` already given back.
    finished: BTreeSet<u64>,
    table: Table,
    wanted: Wanted,
    /// `control.options_binds` is set (the options menu has binds of its own).
    options: bool,
    /// `stop_menu_binds`: `menu_binds`, `typing_binds` and `options_binds` are gone.
    stopped: bool,
}

/// A place in the bind table's queue. Its turn lasts from [`Ticket::wait`] until it is dropped.
pub struct Ticket {
    lock: MenuLock,
    number: u64,
}

impl MenuLock {
    pub fn new() -> MenuLock {
        MenuLock::default()
    }

    fn state(&self) -> MutexGuard<'_, LockState> {
        self.inner
            .state
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
    }

    /// The next place in the queue: taken on the loop when the work is asked for.
    pub fn ticket(&self) -> Ticket {
        let mut st = self.state();
        let number = st.next;
        st.next += 1;
        Ticket {
            lock: self.clone(),
            number,
        }
    }

    /// What the table holds now.
    pub fn table(&self) -> Table {
        self.state().table
    }

    /// Changes what the table holds (a worker, in its turn).
    pub fn set_table(&self, change: impl FnOnce(&mut Table)) {
        change(&mut self.state().table);
    }

    /// What the view wants now.
    pub fn wanted(&self) -> Wanted {
        self.state().wanted
    }

    fn want(&self, change: impl FnOnce(&mut Wanted)) {
        change(&mut self.state().wanted);
    }

    fn options(&self) -> bool {
        let st = self.state();
        st.options && !st.stopped
    }

    fn stopped(&self) -> bool {
        self.state().stopped
    }

    /// Waits until every ticket taken before this call has had its turn, for up to `timeout`: false if one is still
    /// out then.
    pub fn settle(&self, timeout: Duration) -> bool {
        self.ticket().wait_until(Some(Instant::now() + timeout))
    }
}

impl Ticket {
    /// Blocks until this ticket's turn.
    pub fn wait(&self) {
        self.wait_until(None);
    }

    /// [`Ticket::wait`], giving up at `deadline`: false then (the turn has not come).
    pub fn wait_until(&self, deadline: Option<Instant>) -> bool {
        let inner = &self.lock.inner;
        let mut st = self.lock.state();
        while st.serving != self.number {
            match deadline {
                None => {
                    st = inner
                        .turn
                        .wait(st)
                        .unwrap_or_else(std::sync::PoisonError::into_inner);
                }
                Some(at) => {
                    let left = at.saturating_duration_since(Instant::now());
                    if left.is_zero() {
                        return false;
                    }
                    st = inner
                        .turn
                        .wait_timeout(st, left)
                        .unwrap_or_else(std::sync::PoisonError::into_inner)
                        .0;
                }
            }
        }
        true
    }
}

impl Drop for Ticket {
    fn drop(&mut self) {
        let mut st = self.lock.state();
        st.finished.insert(self.number);
        while st.finished.first() == Some(&st.serving) {
            st.finished.pop_first();
            st.serving += 1;
        }
        drop(st);
        self.lock.inner.turn.notify_all();
    }
}

/// Work for the bind worker, asked for on the loop.
enum Work {
    /// `_reload_work`: `after_reload` with the typing mode then wanted, typing mode's set if it changed meanwhile,
    /// the panel's modal binds again (`force`).
    Reload {
        keys: KeyArgs,
        reassert: Option<Reassert>,
    },
    /// `_typing_bind_work()`.
    Typing { keys: KeyArgs },
    /// `_menu_bind_work(force)`.
    Menu { force: bool },
}

/// The bind helper's command line, the run's session mode, and the worker that changes the bind table off the loop.
pub struct Binds {
    args: Vec<String>,
    lock: MenuLock,
    work: Option<Sender<(Work, Ticket)>>,
    worker: Option<JoinHandle<()>>,
    /// `installed`: the binds went in at the start, so they come out at the end.
    installed: bool,
}

/// The worker's side: a client of its own, the run's output and its session mode.
struct Worker {
    client: Client,
    args: Vec<String>,
    output: String,
    mode: Option<Mode>,
    sbs_mode: bool,
    lock: MenuLock,
}

/// The bind command `args` names.
fn command(args: &[String]) -> hyprland::Result<BindCommand<'_>> {
    match args {
        [python, client, control_socket, run_id, session] => Ok(BindCommand {
            python,
            client,
            control_socket,
            run_id,
            session,
        }),
        _ => Err(hyprland::Error::Value("invalid bind arguments".into())),
    }
}

fn pairs(binds: &Option<Vec<(String, String)>>) -> Option<Vec<(&str, &str)>> {
    binds
        .as_ref()
        .map(|v| v.iter().map(|(c, o)| (c.as_str(), o.as_str())).collect())
}

/// The helper beside this binary's bundle: `<bundle>/bin/xr-workspace` runs `<bundle>/tools/native_spike/xr_control.py`.
fn helper() -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?;
    Some(exe.parent()?.parent()?.join(HELPER))
}

/// `view.session_mode(next((m for m in client.query('monitors') if m.get('name') == output), None))`; none on an error.
fn session_mode(client: &mut Client, output: &str) -> Option<Mode> {
    let raw = client.query(Query::Monitors, hyprland::DEADLINE).ok()?;
    let monitors = hyprland::monitors(&raw).ok()?;
    monitors
        .iter()
        .find(|m| m.name == output)
        .and_then(hyprland::session_mode)
}

impl Binds {
    /// `bind_args` (the helper, the socket's path, run id, session) and `mode = view.session_mode(...)` (none on a
    /// Pro 2). No socket, or no bundle to find the helper in: no binds.
    pub fn open(run: &mut Run<'_>, socket: Option<&Server>) -> Option<Binds> {
        let socket = socket?.path().to_str()?.to_string();
        let helper = helper()?.to_str()?.to_string();
        let args = vec![
            PYTHON.to_string(),
            helper,
            socket,
            run.run_id.to_string(),
            run.session.to_string(),
        ];
        Some(Binds::open_with(run, args))
    }

    /// [`Binds::open`] with the helper's command line given. The worker's client is pinned to the compositor the
    /// run's client verified; without one (no verified request yet) the off-loop work is not done.
    pub fn open_with(run: &mut Run<'_>, args: Vec<String>) -> Binds {
        let mode = (run.model != "pro2")
            .then(|| session_mode(run.client, run.output))
            .flatten();
        let lock = MenuLock::new();
        let mut binds = Binds {
            args: args.clone(),
            lock: lock.clone(),
            work: None,
            worker: None,
            installed: false,
        };
        let client = run.client.identity().cloned().and_then(|identity| {
            Client::pinned(run.client.instance(), run.runtime, identity, run.session).ok()
        });
        if let Some(client) = client {
            let mut worker = Worker {
                client,
                args,
                output: run.output.to_string(),
                mode,
                sbs_mode: run.sbs_mode,
                lock,
            };
            let (work, inbox) = mpsc::channel::<(Work, Ticket)>();
            let spawned = thread::Builder::new()
                .name("xr-binds".into())
                .spawn(move || {
                    for (job, ticket) in inbox {
                        ticket.wait();
                        worker.run(job);
                        drop(ticket);
                    }
                });
            if let Ok(handle) = spawned {
                binds.work = Some(work);
                binds.worker = Some(handle);
            }
        }
        binds
    }

    /// `bind_args`: the bind helper's command line (`python, client, control_socket, run_id, session`), which the key
    /// bind panel's worker binds with.
    pub fn bind_args(&self) -> Vec<String> {
        self.args.clone()
    }

    /// The bind table's lock, for the key bind panel's worker and the view.
    pub fn lock(&self) -> &MenuLock {
        &self.lock
    }

    fn send(&self, work: Work) {
        if let Some(sender) = &self.work {
            let _ = sender.send((work, self.lock.ticket()));
        }
    }

    /// `client.install_view_binds(*bind_args, **control.keys.bind_kwargs())`. An error ends the run's start, as the
    /// Python's raises out of `view_controls`.
    pub fn install(&mut self, run: &mut Run<'_>, keys: Option<&Keys>) -> io::Result<()> {
        let (run_binds, send_binds) = keys.map(Keys::bind_kwargs).unwrap_or_default();
        let cmd = command(&self.args).map_err(io::Error::other)?;
        run.client
            .install_view_binds(
                &cmd,
                pairs(&run_binds).as_deref(),
                pairs(&send_binds).as_deref(),
            )
            .map_err(io::Error::other)?;
        self.installed = true;
        Ok(())
    }

    /// `on_events`'s reload: the runtime binds were dropped (`_menu_binds_installed = False`), so `_reload_work` runs
    /// `after_reload` (the binds for the current mode with `keys`' keys, the presentation parked, the session or wide
    /// mode back, then `reassert`), then typing mode's set and the panel's modal binds. The view then calls
    /// `keys.after_reload(host)`, whose work the lock orders after this.
    pub fn after_reload(&mut self, keys: Option<&Keys>, reassert: Option<Reassert>) {
        self.lock.set_table(|t| t.menu = false);
        let keys = keys.map(Keys::bind_kwargs).unwrap_or_default();
        self.send(Work::Reload { keys, reassert });
    }

    /// `_sync_menu_binds(force)`: the placement panel's and the options menu's modal binds to match `menu`, off the
    /// loop. The worker puts in place the state current when it runs, so a fast open/close/open cannot leave the
    /// binds out of step with the panel.
    pub fn sync_menu(&mut self, menu: MenuState, force: bool) {
        self.lock.want(|w| {
            w.menu_open = menu.menu_open;
            w.menu_keys_off = menu.menu_keys_off;
            w.options_owned = menu.options_owned;
        });
        self.send(Work::Menu { force });
    }

    /// `_typing_toggle`'s `_sync_typing_binds()`: typing mode holds only Ctrl+Alt+T (`on`), or the full set again,
    /// with `keys`' keys.
    pub fn typing(&mut self, on: bool, keys: Option<&Keys>) {
        self.lock.want(|w| w.typing = on);
        let keys = keys.map(Keys::bind_kwargs).unwrap_or_default();
        self.send(Work::Typing { keys });
    }

    /// `control.options_binds = options_binds`: the options menu's binds go in while it holds the keyboard.
    pub fn enable_options(&mut self) {
        self.lock.state().options = true;
    }

    /// `join_focus`: waits (up to `stop_menu_binds`' bound) for the bind work asked for so far, the key bind panel's
    /// included. False if some is still out.
    pub fn settle(&self) -> bool {
        self.lock.settle(STOP_TIMEOUT)
    }

    /// `control.stop_menu_binds()`: no later panel, typing or reload change, and the work in flight waited for
    /// (bounded), so none outlives the run.
    pub fn stop_menu_binds(&mut self) {
        self.lock.state().stopped = true;
        self.lock.settle(STOP_TIMEOUT);
        self.work = None; // the worker ends after the work it has, which now does nothing
        if let Some(worker) = self.worker.take()
            && worker.is_finished()
        {
            let _ = worker.join();
        }
    }

    /// `client.remove_view_binds()` (an open panel's modal binds too) if they went in; errors ignored.
    pub fn remove(&mut self, run: &mut Run<'_>) {
        if std::mem::take(&mut self.installed) {
            let _ = run.client.remove_view_binds();
        }
    }
}

impl Drop for Binds {
    fn drop(&mut self) {
        self.lock.state().stopped = true;
    }
}

impl Worker {
    fn run(&mut self, work: Work) {
        if self.lock.stopped() {
            return;
        }
        match work {
            Work::Reload { keys, reassert } => self.reload(&keys, reassert),
            Work::Typing { keys } => self.typing(&keys, false),
            Work::Menu { force } => self.menu(force),
        }
    }

    /// `_reload_work`.
    fn reload(&mut self, keys: &KeyArgs, reassert: Option<Reassert>) {
        let typing = self.lock.wanted().typing;
        let _ = self.after_reload(typing, keys, reassert); // best effort; the run itself is unaffected
        self.lock.set_table(|t| t.typing = typing);
        self.typing(keys, false); // Ctrl+Alt+T pressed during the reload: the matching set
        self.menu(true); // after the view binds: an open panel's modal keys again
    }

    /// `managed.after_reload`: a config reload dropped the runtime binds and reset the session mode.
    fn after_reload(
        &mut self,
        typing: bool,
        keys: &KeyArgs,
        reassert: Option<Reassert>,
    ) -> hyprland::Result<()> {
        let cmd = command(&self.args)?;
        let (run_binds, send_binds) = (pairs(&keys.0), pairs(&keys.1));
        self.client.reinstall_view_binds(
            &cmd,
            typing,
            run_binds.as_deref(),
            send_binds.as_deref(),
        )?;
        // Park first: a mode restore followed by parking could read the pre-restore mode and overwrite the
        // requested 120 Hz rule.
        self.client.park_presentation(&self.output)?;
        if let Some(mode) = &self.mode {
            let raw = self.client.query(Query::Monitors, RELOAD_READ)?;
            let now = hyprland::monitors(&raw)?
                .into_iter()
                .find(|m| m.name == self.output);
            // Glasses side by side have no session mode to go back to: their wide one instead, 3D or not.
            let wide = match (&now, self.sbs_mode) {
                (Some(m), true) => hyprland::best_wide(m, WIDE_MODEL)?,
                _ => None,
            };
            if let Some(rate) = wide {
                self.client
                    .apply_wide_mode(&self.output, rate, WIDE_MODEL)?;
            } else if now.as_ref().and_then(hyprland::session_mode).as_ref() != Some(mode) {
                self.client.apply_session_mode(&self.output)?;
            }
        }
        if let Some(reassert) = reassert {
            reassert();
        }
        Ok(())
    }

    /// `_typing_bind_work(force)`.
    fn typing(&mut self, keys: &KeyArgs, force: bool) {
        let want = self.lock.wanted().typing;
        if want == self.lock.table().typing && !force {
            return;
        }
        let Ok(cmd) = command(&self.args) else { return };
        let (run_binds, send_binds) = (pairs(&keys.0), pairs(&keys.1));
        let done = self.client.reinstall_view_binds(
            &cmd,
            want,
            run_binds.as_deref(),
            send_binds.as_deref(),
        );
        if done.is_ok() {
            // Best effort otherwise; Ctrl+Alt+T again retries.
            self.lock.set_table(|t| {
                t.typing = want;
                if want {
                    t.menu = false; // the swap removed those too
                }
            });
        }
    }

    /// `_menu_bind_work(force)`: one table, one writer, off before on (`Keybind:remove()` takes a chord in whichever
    /// set holds it).
    fn menu(&mut self, force: bool) {
        let wanted = self.lock.wanted();
        let options = self.lock.options();
        let owned = wanted.options_owned;
        let want = wanted.menu_open && !wanted.menu_keys_off && !owned;
        let _ = (|| -> hyprland::Result<()> {
            // Best effort: a click or Ctrl+Alt+L still closes a menu.
            if options && self.lock.table().options && !owned {
                self.client.remove_menu_binds()?;
                self.lock.set_table(|t| t.options = false);
            }
            let table = self.lock.table();
            if want != table.menu || (force && want) {
                if want {
                    self.client.install_menu_binds(&command(&self.args)?)?;
                } else {
                    self.client.remove_menu_binds()?;
                }
                self.lock.set_table(|t| t.menu = want);
            }
            if options && owned && (force || !self.lock.table().options) {
                self.client.install_options_binds(&command(&self.args)?)?;
                self.lock.set_table(|t| t.options = true);
            }
            Ok(())
        })();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tickets_take_their_turns_in_the_order_they_were_taken() {
        let lock = MenuLock::new();
        let (first, second, third) = (lock.ticket(), lock.ticket(), lock.ticket());
        let order = Arc::new(Mutex::new(Vec::new()));
        let later = {
            let order = order.clone();
            thread::spawn(move || {
                third.wait();
                order.lock().unwrap().push(3);
            })
        };
        let middle = {
            let order = order.clone();
            thread::spawn(move || {
                second.wait();
                order.lock().unwrap().push(2);
            })
        };
        thread::sleep(Duration::from_millis(20));
        assert!(
            order.lock().unwrap().is_empty(),
            "no turn before the first ends"
        );
        first.wait();
        order.lock().unwrap().push(1);
        drop(first);
        middle.join().unwrap();
        later.join().unwrap();
        assert_eq!(*order.lock().unwrap(), [1, 2, 3]);
        assert!(lock.settle(Duration::from_millis(100)));
    }

    #[test]
    fn a_ticket_given_up_unused_does_not_hold_the_queue() {
        let lock = MenuLock::new();
        let (first, second) = (lock.ticket(), lock.ticket());
        drop(second); // never waited on: its turn is given up
        assert!(
            !lock.settle(Duration::from_millis(20)),
            "the first is still out"
        );
        drop(first);
        assert!(lock.settle(Duration::from_millis(100)));
    }

    #[test]
    fn the_flags_and_the_wanted_state_are_shared() {
        let lock = MenuLock::new();
        let other = lock.clone();
        other.set_table(|t| t.typing = true);
        lock.want(|w| w.menu_open = true);
        assert!(lock.table().typing);
        assert!(other.wanted().menu_open);
    }
}
