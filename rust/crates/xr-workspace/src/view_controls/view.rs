//! `view.ViewControl` (tools/native_spike/view.py from `class ViewControl`): the run's view state on the loop, the
//! dispatcher of every user op, the renderer frames, the status reply, the timed work (`poll`), the Hyprland events,
//! and the setup saved for the next start. With it from tools/native_spike/managed.py: the saved view settings
//! (`store.load()`), `_restored`, `_initial_placements` and the event stream (`client.event_stream`); from
//! tools/native_spike/supervisor.py the status datagrams it takes (`Supervisor.status_bytes`: the measured status for
//! `status_reply`, `overview_geometry`) and `_events_ready`.
//!
//! Ported: the view's own ops (`view`, `recenter`, `help` and its pages, typing mode, the pill panel's keys,
//! `placement`, `stabilization`, the background), their frames (`view`, `recenter`, `style`, `help`, `placement`,
//! `placement_menu`, `environment`), `initial_frame`, `status_reply`, `poll` (the debounced saves, the overlay idle
//! close, the key bind panel's and the window memory's polls), `on_events` (a config reload's style frame, window
//! events for the window memory), `flush`, `save_setup`, `window_context`, `renderer_restarted`, `_restored` and
//! `_initial_placements`, the profile stores (`ProfileStore`, `PlacementStore`: `setup::store`), and the key bind
//! panel's [`Host`](crate::supervisor::keybinds::Host).
//!
//! Not ported here: the options menu (`options_menu.OptionsMenu`, the `options_*` ops, `RuntimeText`), whose
//! `keyboard_owned` is always false; and the parts that are another feature's state, each marked where it goes: the
//! overview pointer's (its rolls, `summon`, `level`, the geometry it hit-tests on, and so bumping and `_settle`), the
//! desktops' (the panel's desktop row, Ctrl+Alt+R), 3D's (`_immersive_sync`, the status reply's run fields) and the
//! binds' (the panel's and typing mode's bind swaps, `after_reload`).

use std::os::fd::{AsFd, AsRawFd, RawFd};
use std::path::PathBuf;
use std::sync::{Arc, Mutex, PoisonError};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use serde::Serialize;
use xr_wire::run_control::Frame;

use super::{Features, Frames, Run, setup};
use crate::geometry::{DEFAULT_PLACEMENT, Desktop, Placement};
use crate::hyprland::{Client, EventStream, Query};
use crate::pyjson::{self, Json};
use crate::supervisor::environment::{self, BRIGHTNESS_STEPS, Environment};
use crate::supervisor::immersive;
use crate::supervisor::view::{
    self as vs, Field, GLIDE_MS, MENU_OPS, Mode, ViewOptions, ViewPayload, ViewState,
};

pub use crate::lua::resize::{CANCEL_OP as RESIZE_CANCEL_OP, RESIZE_OPS};
pub use crate::supervisor::privacy::PRIVACY_OP;
/// `view.IMMERSIVE_OP` and `IMMERSIVE_SET_OP`.
pub const IMMERSIVE_OP: &str = "immersive_toggle";
pub const IMMERSIVE_SET_OP: &str = "immersive";
/// `view.BINDS_PANEL_OP` and the key bind panel's key (`keybinds.OPS`).
pub const BINDS_PANEL_OP: &str = "binds_toggle";
pub const BINDS_KEY_OP: &str = "binds_key";
/// `view.RESET_OP`: Ctrl+Alt+R.
pub const RESET_OP: &str = "layout_reset";
/// `view.ENVIRONMENT_OP` and `view.PLAYBACK_MENU_OP`.
pub const ENVIRONMENT_OP: &str = environment::TOGGLE_OP;
pub const ENVIRONMENT_SET_OP: &str = environment::SET_OP;
pub const PLAYBACK_MENU_OP: &str = "playback_menu_toggle";
/// `view.TYPING_OP`: Ctrl+Alt+T.
pub const TYPING_OP: &str = "typing_toggle";
/// `view.HELP_PAGE_OPS`.
pub const HELP_PAGE_OPS: [(&str, i64); 2] = [("help_prev", -1), ("help_next", 1)];
/// `view.OVERLAY_IDLE_CLOSE_S`: the help card and an open panel close after this long without input.
pub const OVERLAY_IDLE_CLOSE_S: f64 = 30.0;
/// `view.MENU_SAVE_DELAY`: a panel change is saved this long after the last one.
pub const MENU_SAVE_DELAY: f64 = 0.5;
/// `view.MODE_OVERVIEW_TOAST`.
pub const MODE_OVERVIEW_TOAST: &str = "The overview always stays fixed in the room (anchor). Maximize a desktop \
    (select it, then Ctrl+Alt+Up), then Ctrl+Alt+V switches it between follow and locked; your choice is kept until XR \
    restarts.";
/// `view.PLACE_HELD_TOAST`.
pub const PLACE_HELD_TOAST: &str =
    "Placement change kept: it applies when you go back to the overview.";
/// `view.MENU_DESKTOP_OPS`: the open panel's desktop row on keys (the desktops' port).
const MENU_DESKTOP_OPS: [&str; 3] = ["menu_ultrawide", "menu_add", "menu_delete"];
/// `view.WINDOW_EVENT`'s events: what changes where windows are.
const WINDOW_EVENTS: [&str; 11] = [
    "openwindow",
    "closewindow",
    "movewindow",
    "movewindowv2",
    "changefloatingmode",
    "fullscreen",
    "workspace",
    "workspacev2",
    "moveworkspace",
    "moveworkspacev2",
    "renameworkspace",
];
/// `getoption`'s timeout for the theme's border (`border=lambda: client.query(..., timeout=0.5)`).
const BORDER_TIMEOUT: Duration = Duration::from_millis(500);

/// `view.IGNORED_WHILE_TYPING`: MENU_OPS + SEND_OPS + privacy, the playback menu, the key bind panel, the background
/// and 3D toggles.
pub fn ignored_while_typing(op: &str) -> bool {
    MENU_OPS.contains(&op)
        || crate::lua::send::SEND_OPS.contains(&op)
        || [
            PRIVACY_OP,
            PLAYBACK_MENU_OP,
            BINDS_PANEL_OP,
            BINDS_KEY_OP,
            ENVIRONMENT_OP,
            IMMERSIVE_OP,
        ]
        .contains(&op)
}

/// A seconds clock (`clock=time.monotonic`).
pub type Clock = Box<dyn Fn() -> f64 + Send>;
/// A toast (`control.notify`, `send.notify` in a run), sent off the loop.
pub type Notify = Arc<dyn Fn(&str) + Send + Sync>;
/// `focus(name)`: the output takes Hyprland's focus (a compositor call, off the loop).
pub type Focus = Arc<dyn Fn(&str) + Send + Sync>;
/// `border()`: Hyprland's `getoption general:col.active_border` reply, `None` on an error.
pub type Border = Box<dyn FnMut() -> Option<Json> + Send>;

/// Where the view's settings and the monitor setup are saved.
#[derive(Clone, Debug, PartialEq)]
pub enum Store {
    /// `profiles.ProfileStore(path, model)` (with its `PlacementStore`): a run's.
    Profile { path: PathBuf, model: String },
    /// `profiles.LayoutStore(path)`: `layout.json`, no monitor setup.
    Layout(PathBuf),
}

impl Store {
    /// `store.load()`.
    pub fn load(&self) -> vs::Settings {
        match self {
            Store::Profile { path, model } => setup::store::load_view(path, model),
            Store::Layout(path) => vs::load_settings(path),
        }
    }

    /// `settings.save(state.settings())`.
    fn save(&self, settings: &vs::Settings) -> Result<(), String> {
        match self {
            Store::Profile { path, model } => setup::store::save_view(path, model, settings),
            Store::Layout(path) => vs::save_settings(path, settings).map_err(|e| e.to_string()),
        }
    }

    /// The `PlacementStore` beside a profile; a layout file has none.
    fn placements(&self) -> Option<(&std::path::Path, &str)> {
        match self {
            Store::Profile { path, model } => Some((path, model)),
            Store::Layout(_) => None,
        }
    }
}

/// `managed._restored(store, names, desktops)`: the saved setup's placements and rolls when its desktops are the run's
/// own (`live`: the run's, else the count alone is compared), else flat and level. The reason a saved setup is not
/// used is logged.
pub fn restored(
    store: &Store,
    names: &[String],
    live: Option<&[Desktop]>,
) -> Option<(Vec<Placement>, Vec<i64>)> {
    let (path, model) = store.placements()?;
    let flat = (vec![DEFAULT_PLACEMENT; names.len()], vec![0; names.len()]);
    let (saved, reason) = setup::load_setup(path, model);
    match reason.as_deref() {
        Some(setup::NOTHING_SAVED) => return Some(flat),
        Some(reason) => {
            eprintln!("xr-workspace: run: saved monitor setup not used: {reason}; flat placements");
            return Some(flat);
        }
        None => {}
    }
    if saved.desktops.len() != names.len() || live.is_some_and(|l| l != saved.desktops) {
        let sizes: Vec<String> = saved
            .desktops
            .iter()
            .map(|d| {
                // `f"{scale:g}"`: the scales are short decimals (1, 1.25, 1.5 ...).
                let scale = if d.scale.fract() == 0.0 {
                    format!("{}", d.scale as i64)
                } else {
                    pyjson::float_repr(d.scale)
                };
                format!("{}x{}@{scale}", d.width, d.height)
            })
            .collect();
        let n = saved.desktops.len();
        eprintln!(
            "xr-workspace: run: the saved placements are for other desktops ({n} desktop{}: {}); flat placements",
            if n == 1 { "" } else { "s" },
            sizes.join(", ")
        );
        return Some(flat);
    }
    Some((saved.placements, saved.rolls))
}

/// `managed._initial_placements(saved, names)`: one per desktop, missing ones flat.
pub fn initial_placements(saved: &[Placement], count: usize) -> Vec<Placement> {
    let mut out: Vec<Placement> = saved.iter().copied().take(count).collect();
    out.resize(count, DEFAULT_PLACEMENT);
    out
}

/// A cursor switch's flags on the `view` payload (`cursor_switch_flags`, `pan` / `cut` / `center`).
#[derive(Clone, Copy, Default, Serialize)]
struct Flags {
    #[serde(skip_serializing_if = "Option::is_none")]
    pan: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    cut: Option<bool>,
}

#[derive(Serialize)]
struct FlaggedView {
    #[serde(flatten)]
    view: ViewPayload,
    #[serde(flatten)]
    flags: Flags,
}

/// The window memory's context of each desktop, kept current for its worker (`window_context`).
type Context = Arc<Mutex<Option<serde_json::Value>>>;

/// `view.ViewControl`.
pub struct ViewControl {
    pub state: ViewState,
    run_id: String,
    session: String,
    /// `next_id`: the next frame's request id.
    pub next_id: u64,
    clock: Clock,
    settings: Option<Store>,
    focus: Option<Focus>,
    border: Option<Border>,
    pub notify: Option<Notify>,
    /// The last theme colour sent (`accent`).
    accent: Option<[u8; 3]>,
    pub help_visible: bool,
    /// A VR video plays in place of the desktops (`video.VideoControl` sets it).
    pub video_active: bool,
    /// The last run key, click or panel change (the overlay's idle close counts from it).
    pub last_input: f64,
    save_due: Option<f64>,
    environment_save_due: Option<f64>,
    /// A press on a desktop gave the panel's keys back to the window (`desktop_press`).
    menu_keys_off: bool,
    /// The renderer reported a new overview layout (`_settle`, the overview pointer's).
    settle_due: bool,
    /// The run's desktops as the broker has them (`desktops.desktops()`), when the run has a `DesktopManager`.
    live: Option<Vec<Desktop>>,
    /// Each desktop's levelling roll, `state.names` order (`_rolls()`): the overview pointer's (`pointer.rolls`),
    /// which its port keeps here. `None`: the run has no overview pointer.
    pub rolls: Option<Vec<i64>>,
    /// The rolls the last session left (`_restored`), for the overview pointer to start from.
    pub restored_rolls: Vec<i64>,
    rolls_seen: Option<(Vec<String>, Vec<i64>)>,
    /// The measured status the renderer last sent (`Supervisor.status`), as it came.
    measured: Vec<(String, xr_wire::json::Value)>,
    status_buffer: Vec<u8>,
    status_discard: bool,
    events: Option<EventStream>,
    pub reloads: u64,
    /// What the binds last were told (`_menu_binds_installed`, `_typing_installed`, `_options_binds_installed`).
    menu_binds_installed: bool,
    typing_installed: bool,
    options_binds_installed: bool,
    /// The panel's or typing mode's binds changed: [`ViewControl::sync_binds`] tells the binds after the op.
    binds_due: bool,
    context: Context,
    /// `_help_binds`: the key bind panel's keys (run, send) as of the last op or poll; the defaults without it.
    help_binds: Option<(Vec<(String, &'static str)>, Vec<(String, &'static str)>)>,
    threads: Vec<JoinHandle<()>>,
}

impl ViewControl {
    /// `view.ViewControl(view.ViewState(capture, ...saved...), ...)` as `view_controls` builds it: the profile's view
    /// settings (`store.load()`), the placements the last session left (`_restored`, `_initial_placements`), the
    /// run's desktops (`control.state.wide`, `state.sizes`), the theme's border and the focus through the run's
    /// compositor.
    pub fn open(run: &Run<'_>) -> Option<ViewControl> {
        if run.capture.is_empty() {
            return None;
        }
        let store = Store::Profile {
            path: setup::profiles_path()?,
            model: run.model.to_owned(),
        };
        let live: Option<Vec<Desktop>> = (!run.specs.is_empty()).then(|| {
            run.specs
                .iter()
                .map(|s| Desktop {
                    width: s.width,
                    height: s.height,
                    scale: s.scale,
                })
                .collect()
        });
        let compositor = run.client.identity().cloned().map(|identity| {
            (
                run.client.instance().to_owned(),
                run.runtime.to_path_buf(),
                identity,
                run.session.to_owned(),
            )
        });
        let focus: Option<Focus> =
            compositor
                .clone()
                .map(|(instance, runtime, identity, session)| {
                    Arc::new(move |name: &str| {
                        let Ok(command) = vs::focus_command(name) else {
                            return;
                        };
                        if let Ok(mut client) =
                            Client::pinned(&instance, &runtime, identity.clone(), &session)
                        {
                            let _ = client.mutate(&command); // best effort: the view still changes
                        }
                    }) as Focus
                });
        let border: Option<Border> = compositor.map(|(instance, runtime, identity, session)| {
            let mut client = Client::pinned(&instance, &runtime, identity, &session).ok();
            Box::new(move || {
                let reply = client
                    .as_mut()?
                    .query(Query::ActiveBorder, BORDER_TIMEOUT)
                    .ok()?;
                Json::parse(&xr_wire::json::encode(
                    &reply,
                    xr_wire::json::Style::COMPACT,
                ))
                .ok()
            }) as Border
        });
        let mut view = ViewControl::new(
            run.capture,
            run.run_id,
            run.session,
            Some(store),
            live,
            Box::new(monotonic),
        )?;
        view.focus = focus;
        view.border = border;
        view.notify = Some(Arc::new(crate::managed::real::notify));
        Some(view)
    }

    /// The view of `names` with `settings` (the saved view settings and, with a profile, the monitor setup), as
    /// [`ViewControl::open`] builds it, without the compositor. `live`: the run's desktops (its `DesktopManager`'s);
    /// a run with a profile also has an overview pointer's rolls. None for names the view refuses.
    pub fn new(
        names: &[String],
        run_id: &str,
        session: &str,
        settings: Option<Store>,
        live: Option<Vec<Desktop>>,
        clock: Clock,
    ) -> Option<ViewControl> {
        let saved = settings.as_ref().map(Store::load).unwrap_or_default();
        let restored = settings
            .as_ref()
            .and_then(|s| restored(s, names, live.as_deref()));
        let refs: Vec<&str> = names.iter().map(String::as_str).collect();
        let mut state = ViewState::new(
            &refs,
            ViewOptions {
                spacing: saved.spacing,
                prediction: saved.prediction,
                stabilization: saved.stabilization,
                placements: restored
                    .as_ref()
                    .map(|(p, _)| initial_placements(p, names.len()))
                    .unwrap_or_default(),
                video_quality: saved.video_quality,
                environment: saved.environment,
                immersive: saved.immersive,
            },
        )
        .ok()?;
        if let Some(live) = live.as_ref().filter(|l| l.len() == names.len()) {
            state.wide = live.iter().map(|d| d.is_ultrawide()).collect();
            state.sizes = Some(live.clone());
        }
        let restored_rolls = restored.map(|(_, rolls)| rolls).unwrap_or_default();
        let last_input = clock();
        Some(ViewControl {
            state,
            run_id: run_id.to_owned(),
            session: session.to_owned(),
            next_id: 2,
            clock,
            settings,
            focus: None,
            border: None,
            notify: None,
            accent: None,
            help_visible: false,
            video_active: false,
            last_input,
            save_due: None,
            environment_save_due: None,
            menu_keys_off: false,
            settle_due: false,
            live,
            rolls: None,
            restored_rolls,
            rolls_seen: None,
            measured: Vec::new(),
            status_buffer: Vec::new(),
            status_discard: false,
            events: None,
            reloads: 0,
            menu_binds_installed: false,
            typing_installed: false,
            options_binds_installed: false,
            binds_due: false,
            context: Arc::new(Mutex::new(None)),
            help_binds: None,
            threads: Vec::new(),
        })
    }

    /// A view on `names` with the defaults and nothing saved, for the routing tests.
    pub fn with_names(names: Vec<String>) -> ViewControl {
        ViewControl::new(&names, "r1", "s", None, None, Box::new(|| 0.0)).expect("managed names")
    }

    /// The theme's border (`border=lambda: client.query('getoption', ...)`).
    pub fn set_border(&mut self, border: Option<Border>) {
        self.border = border;
    }

    /// `focus(name)`.
    pub fn set_focus(&mut self, focus: Option<Focus>) {
        self.focus = focus;
    }

    /// `control.events = client.event_stream()`; none on an error.
    pub fn open_events(&mut self, run: &mut Run<'_>) {
        self.events = run.client.event_stream().ok();
    }

    /// The event stream, for the loop's poll.
    pub fn events_fd(&self) -> Option<RawFd> {
        self.events.as_ref().map(|e| e.as_fd().as_raw_fd())
    }

    /// `Supervisor._events_ready` and `ViewControl.on_events`: false at the stream's end.
    pub fn events_ready(&mut self, f: &mut Features<'_>, out: &mut Frames) -> bool {
        let Some(events) = self.events.as_mut() else {
            return false;
        };
        match events.read_lines() {
            Ok(Some(lines)) => {
                self.on_events(&lines, f, out);
                true
            }
            _ => false,
        }
    }

    /// `ViewControl.on_events` on the complete lines that came: a config reload re-applies the run's binds and the
    /// style, a window change has the window memory read Hyprland again.
    pub fn on_events(&mut self, lines: &[Vec<u8>], f: &mut Features<'_>, out: &mut Frames) {
        let (mut reloaded, mut moved) = (false, false);
        for line in lines {
            let text = String::from_utf8_lossy(line);
            if text.starts_with("configreloaded>>") {
                reloaded = true;
            } else if WINDOW_EVENTS
                .iter()
                .any(|e| text.strip_prefix(e).is_some_and(|r| r.starts_with(">>")))
            {
                moved = true;
            }
            // `xrcursor>>`: the overview pointer's (`push_shape`).
        }
        if moved && let Some(windows) = f.windows.as_deref_mut() {
            windows.mark();
        }
        if reloaded {
            self.reloads += 1;
            self.menu_binds_installed = false; // a reload drops runtime binds; the reload work re-adds an open panel's
            // `_reload_work`: `on_reload` (the binds' `after_reload`), then the typing set and the panel's keys.
            if let Some(binds) = f.binds.as_deref_mut() {
                binds.after_reload(self.state.typing, f.keys.as_deref());
            }
            self.typing_installed = self.state.typing;
            self.binds_due = true;
            if let Some(keys) = f.keys.as_deref_mut() {
                keys.after_reload(self); // an open key bind panel's key subscription
            }
            out.bytes.extend(self.style_frame());
        }
        self.sync_binds(f, reloaded); // after a reload: an open panel's modal keys again
    }

    /// `events.close()`.
    pub fn close_events(&mut self) {
        self.events = None;
    }

    /// `control.options_key_text = RuntimeText(control, client)` and `control.options_binds`: the options menu's,
    /// not ported.
    pub fn open_options(&mut self, _run: &mut Run<'_>) {}

    /// The `finally`'s first block: the options menu closed, its binds removed, the maximize geometry cleared on the
    /// pointer. Nothing while the options menu is not ported.
    pub fn close_options(
        &mut self,
        _run: &mut Run<'_>,
        _pointer: Option<&mut super::overview_pointer::Pointer>,
    ) {
    }

    fn now(&self) -> f64 {
        (self.clock)()
    }

    fn take_id(&mut self) -> u64 {
        let id = self.next_id;
        self.next_id += 1;
        id
    }

    fn tell(&mut self, text: &str) {
        let Some(notify) = self.notify.clone() else {
            return;
        };
        let text = text.to_owned();
        self.threads.retain(|t| !t.is_finished());
        if let Ok(t) = std::thread::Builder::new()
            .name("xr-view-tell".into())
            .spawn(move || notify(&text))
        {
            self.threads.push(t);
        }
    }

    fn save_settings(&self) {
        if let (Some(store), Ok(settings)) = (&self.settings, self.state.settings()) {
            let _ = store.save(&settings); // best effort: the run keeps going
        }
    }

    // --- frames ---------------------------------------------------------------------------------------------------

    /// `_frame(**flags)`: a view frame, with a cursor switch's flags.
    fn view_frame(&mut self, flags: Flags) -> Vec<u8> {
        let id = self.take_id();
        let payload = FlaggedView {
            view: self.state.view(),
            flags,
        };
        vs::frame(&self.run_id, &self.session, id, "view", payload).into_bytes()
    }

    fn placement_frame(&mut self, initial: bool, glide: bool) -> Vec<u8> {
        if initial
            && self
                .state
                .placements
                .iter()
                .all(|p| *p == DEFAULT_PLACEMENT)
        {
            return Vec::new(); // the renderer starts flat
        }
        let id = self.take_id();
        let glide = glide.then_some(GLIDE_MS);
        vs::placement_frame(
            &self.run_id,
            &self.session,
            id,
            &self.state.placements,
            glide,
        )
        .map(String::into_bytes)
        .unwrap_or_default()
    }

    fn menu_frame(&mut self) -> Vec<u8> {
        let id = self.take_id();
        vs::placement_menu_frame(&self.run_id, &self.session, id, &self.state.menu())
            .map(String::into_bytes)
            .unwrap_or_default()
    }

    fn environment_frame(&mut self, initial: bool) -> Vec<u8> {
        let env = self.state.environment.to_json();
        if initial && !environment::on(&env) {
            return Vec::new();
        }
        let id = self.take_id();
        environment::renderer_frame(&self.run_id, &self.session, id, &env).unwrap_or_default()
    }

    /// `_style_frame`: the theme's border colour for the highlight; nothing if unknown or unchanged.
    fn style_frame(&mut self) -> Vec<u8> {
        let Some(border) = self.border.as_mut() else {
            return Vec::new();
        };
        let rgb = border().and_then(|reply| vs::parse_border(&reply));
        let Some(rgb) = rgb.filter(|rgb| Some(*rgb) != self.accent) else {
            return Vec::new();
        };
        self.accent = Some(rgb);
        let id = self.take_id();
        vs::style_frame(&self.run_id, &self.session, id, rgb).into_bytes()
    }

    /// `_help_binds`, read from the key bind panel while the view has it.
    fn note_keys(&mut self, f: &Features<'_>) {
        self.help_binds = f
            .keys
            .as_deref()
            .map(|k| (k.panel().run_binds(), k.panel().send_binds()));
    }

    fn help_frame(&mut self, visible: bool, turn: i64) -> Vec<u8> {
        // `_help_binds`: the key bind panel's keys (every bind, on or off); the defaults without the panel.
        let id = self.take_id();
        let borrowed = self.help_binds.as_ref().map(|(r, s)| {
            (
                r.iter().map(|(c, o)| (c.as_str(), *o)).collect::<Vec<_>>(),
                s.iter().map(|(c, o)| (c.as_str(), *o)).collect::<Vec<_>>(),
            )
        });
        vs::help_frame(
            &self.run_id,
            &self.session,
            id,
            visible,
            turn,
            borrowed.as_ref().map(|(r, _)| r.as_slice()),
            borrowed.as_ref().map(|(_, s)| s.as_slice()),
        )
        .map(String::into_bytes)
        .unwrap_or_default()
    }

    /// `ViewControl.initial_frame()`: the saved spacing and the overview, the placements, the style, the background
    /// (3D's `_immersive_sync` sends nothing while nothing is 3D); the open panel for a new renderer mid-run.
    pub fn initial_frame(&mut self) -> Vec<u8> {
        let mut out = self.view_frame(Flags::default());
        out.extend(self.placement_frame(true, false));
        out.extend(self.style_frame());
        out.extend(self.environment_frame(true));
        if self.state.menu_open {
            out.extend(self.menu_frame());
        }
        out
    }

    /// `ViewControl.renderer_restarted()`: a new renderer in the same run starts knowing what `initial_frame` says.
    pub fn renderer_restarted(&mut self) {
        self.help_visible = false;
    }

    // --- the status ----------------------------------------------------------------------------------------------

    /// `ViewControl.status_reply(status)`: the `status` answer's body. `held`: asked during a hold
    /// (`serve_without_renderer`), whose `status` is `{}` rather than the renderer's last measured one.
    pub fn status_reply(&self, held: bool) -> Vec<(String, xr_wire::json::Value)> {
        let s = &self.state;
        let mut capabilities: Vec<&str> = vs::USER_OPS.to_vec();
        capabilities.push("options_runtime_complete");
        let names: Vec<&str> = s.names.iter().map(String::as_str).collect();
        let setting = immersive::validate(&s.immersive.to_value()).unwrap_or_default();
        // `_immersive_status` with the run's `ImmersiveRun` idle: no engine installed, nothing switching (3D's port
        // gives its own phase, button and engine).
        let immersive = serde_json::json!({
            "setting": setting.to_value(),
            "names": immersive::on_names(&s.immersive, &names),
            "button": "R1", "switching": false, "stereo": false, "failed": false, "phase": "off",
            "installed": false,
        });
        let status = if held {
            xr_wire::json::Value::Object(Vec::new())
        } else {
            xr_wire::json::Value::Object(self.measured.clone())
        };
        let mut reply = vec![
            ("capabilities".to_owned(), wire(&capabilities)),
            ("view".to_owned(), wire(&s.view())),
            ("status".to_owned(), status),
            ("placements".to_owned(), wire(&s.wanted_placements())),
            ("names".to_owned(), wire(&s.names)),
            ("immersive".to_owned(), wire(&immersive)),
        ];
        if let Some(live) = &self.live {
            reply.push(("desktops".to_owned(), wire(live))); // live sizes (Ultrawide, Add)
        }
        reply
    }

    /// The view's side of `Supervisor.status_bytes`: the measured status kept for `status_reply`, and a new overview
    /// layout noted (`set_overview_geometry`). The other datagrams are the options menu's and the pointer's.
    pub fn status(&mut self, datagram: &[u8], _features: &mut Features<'_>) {
        for chunk in datagram.split_inclusive(|&b| b == b'\n') {
            if !self.status_discard {
                self.status_buffer.extend_from_slice(chunk);
            }
            if self.status_buffer.len() > xr_wire::status::LINE_LIMIT {
                self.status_buffer.clear();
                self.status_discard = true;
            }
            if !chunk.ends_with(b"\n") {
                continue;
            }
            if !self.status_discard {
                let line = std::mem::take(&mut self.status_buffer);
                match xr_wire::status::decode(&line) {
                    Ok(xr_wire::status::Datagram::Status(_)) => {
                        if let Ok(xr_wire::json::Value::Object(entries)) =
                            xr_wire::json::parse_strict(&line)
                        {
                            self.measured = entries;
                        }
                    }
                    Ok(xr_wire::status::Datagram::OverviewGeometry(_)) => self.settle_due = true,
                    _ => {}
                }
            }
            self.status_buffer.clear();
            self.status_discard = false;
        }
    }

    // --- the timed work ------------------------------------------------------------------------------------------

    /// When [`ViewControl::poll`] is next due: a pending save, the overlay's idle close, the key bind panel's and the
    /// window memory's work.
    pub fn deadline(&mut self, f: &mut Features<'_>) -> Option<Instant> {
        let (now, wall) = (self.now(), Instant::now());
        let at = |due: f64| wall + Duration::from_secs_f64((due - now).max(0.0));
        let overlay = (self.state.menu_open || self.help_visible)
            .then_some(self.last_input + OVERLAY_IDLE_CLOSE_S);
        let timed = [self.save_due, self.environment_save_due, overlay]
            .into_iter()
            .flatten()
            .map(at);
        let keys = f.keys.as_deref().and_then(|k| k.deadline(wall));
        let windows = f
            .windows
            .as_deref()
            .filter(|_| !self.state.desktops_busy)
            .and_then(|w| w.deadline());
        timed.chain(keys).chain(windows).min()
    }

    /// `ViewControl.poll(now)`: the debounced saves once due, the window memory's capture, the help card and an open
    /// panel closed after [`OVERLAY_IDLE_CLOSE_S`] without input, the key bind panel's work.
    pub fn poll(&mut self, at: Instant, f: &mut Features<'_>, out: &mut Frames) {
        let now = self.now();
        self.note_keys(f);
        // Ctrl+Alt+R's ease (desktops), the overview pointer's poll: their ports.
        self.watch_rolls(now);
        if self.save_due.is_some_and(|due| now >= due) {
            self.flush();
        }
        if self.environment_save_due.is_some_and(|due| now >= due) {
            self.save_environment();
        }
        // The desktops' result, `_settle` (the pointer's geometry), `_immersive_tick`: their ports.
        if !self.state.desktops_busy
            && let Some(windows) = f.windows.as_deref_mut()
        {
            self.publish_context();
            windows.poll(at); // off the loop; never mid-change
        }
        if now - self.last_input >= OVERLAY_IDLE_CLOSE_S {
            if self.state.menu_open {
                let frames = self.on_menu("menu_toggle", f); // saves at once and releases the panel keys
                out.bytes.extend(frames);
            }
            if self.help_visible {
                let frames = self.help_toggle(f);
                out.bytes.extend(frames);
            }
        }
        if let Some(keys) = f.keys.as_deref_mut() {
            keys.poll(now, self, out); // its own idle close, a change's timeout
        }
        self.sync_binds(f, false);
    }

    /// `_watch_rolls`: a levelling roll changed (the overview pointer's level bars) is saved like a placement.
    fn watch_rolls(&mut self, now: f64) {
        let Some(rolls) = self.rolls.clone() else {
            return;
        };
        let seen = self
            .rolls_seen
            .replace((self.state.names.clone(), rolls.clone()));
        if let Some((names, before)) = seen
            && names == self.state.names
            && before != rolls
        {
            self.save_due = Some(now + MENU_SAVE_DELAY);
        }
    }

    /// `control.flush()`: a pending panel or drag save now, and a pending brightness.
    pub fn flush(&mut self) {
        if self.environment_save_due.is_some() {
            self.save_environment();
        }
        if self.save_due.take().is_some() {
            self.save_placements();
        }
    }

    fn save_environment(&mut self) {
        self.environment_save_due = None;
        self.save_settings();
    }

    /// `_save_placements`: the placements, with the rolls, into the profile.
    fn save_placements(&mut self) {
        let Some((path, model)) = self.settings.as_ref().and_then(Store::placements) else {
            return;
        };
        let _ =
            setup::store::save_layout(path, model, &self.state.placements, self.rolls.as_deref());
    }

    /// `control.save_setup()`: the whole monitor setup for the next start (profiles.py), best effort.
    pub fn save_setup(&mut self) {
        self.publish_context();
        let Some((path, model)) = self.settings.as_ref().and_then(Store::placements) else {
            return;
        };
        let placements = self.state.wanted_placements(); // a change still held: the next start's
        let rolls = self.rolls.as_deref();
        let _ = match self.live.as_deref() {
            Some(live) if live.len() == placements.len() => {
                setup::store::save_desktops(path, model, live, &placements, rolls)
            }
            _ => setup::store::save_layout(path, model, &placements, rolls),
        };
    }

    /// `window_context()`: each desktop's placement and levelling roll, as the window memory keeps them with it.
    fn context_now(&self) -> serde_json::Value {
        let s = &self.state;
        let mut map = serde_json::Map::new();
        for (i, (name, p)) in s.names.iter().zip(&s.placements).enumerate() {
            let roll = self
                .rolls
                .as_ref()
                .and_then(|r| r.get(i))
                .copied()
                .unwrap_or(0);
            map.insert(
                name.clone(),
                serde_json::json!({"placement": serde_json::to_value(p).unwrap_or_default(), "roll": roll}),
            );
        }
        serde_json::Value::Object(map)
    }

    fn publish_context(&self) {
        *self.context.lock().unwrap_or_else(PoisonError::into_inner) = Some(self.context_now());
    }

    /// `windows.context = control.window_context`: the window memory's worker reads the view's context as it was
    /// last published (on each poll, and at the run's end before its last capture).
    pub fn window_context(&self) -> super::window_memory::Context {
        self.publish_context();
        let context = Arc::clone(&self.context);
        Arc::new(move || {
            context
                .lock()
                .unwrap_or_else(PoisonError::into_inner)
                .clone()
        })
    }

    /// `_desktops_changed`: the run's desktops changed (the desktops' port calls it); the sender's worker knows them.
    pub fn desktops_changed(&mut self, f: &mut Features<'_>) {
        if let Some(worker) = f.send.as_deref_mut().and_then(|s| s.worker()) {
            worker.set_names(&self.state.names);
        }
    }

    // --- binds --------------------------------------------------------------------------------------------------

    /// `_sync_menu_binds` and `_sync_typing_binds`: the binds follow the panel and typing mode (`_menu_bind_work`,
    /// `_typing_bind_work`). `force`: after a reload, an open panel's keys are added again.
    fn sync_binds(&mut self, f: &mut Features<'_>, force: bool) {
        if !self.state.menu_open {
            self.menu_keys_off = false; // the next panel starts with its keys
        }
        if !std::mem::take(&mut self.binds_due) && !force {
            return;
        }
        let Some(binds) = f.binds.as_deref_mut() else {
            return;
        };
        if self.state.typing != self.typing_installed {
            binds.typing_binds(self.state.typing, f.keys.as_deref());
            self.typing_installed = self.state.typing;
            if self.state.typing {
                self.menu_binds_installed = false; // the swap removed those too
            }
        }
        let want = self.state.menu_open && !self.menu_keys_off;
        if want != self.menu_binds_installed || (force && want) {
            binds.menu_binds(want);
            self.menu_binds_installed = want;
        }
    }

    // --- ops ----------------------------------------------------------------------------------------------------

    /// `ViewControl.on_message`, in its order. Each arm that is another feature's calls that feature.
    pub fn on_message(&mut self, message: &Frame, f: &mut Features<'_>, out: &mut Frames) {
        let op = message.op.as_str();
        if op.starts_with("options_") {
            return; // the options menu's (not ported); `binds_key` and `menu_back` reach it only while it owns keys
        }
        self.last_input = self.now(); // any run key, click or panel change keeps an open card or panel up
        self.note_keys(f);
        let frames = self.route(message, f, out);
        out.bytes.extend(frames);
        self.sync_binds(f, false);
    }

    fn route(&mut self, message: &Frame, f: &mut Features<'_>, out: &mut Frames) -> Vec<u8> {
        let op = message.op.as_str();
        let payload = plain(&message.payload);
        if op == "stabilization" {
            return self.on_stabilization(&payload);
        }
        if op == "placement" {
            return self.on_placement(&payload);
        }
        if op == RESIZE_CANCEL_OP {
            if let Some(pointer) = f.pointer.as_deref_mut() {
                pointer.super_down(out);
            }
            return Vec::new();
        }
        if op == TYPING_OP {
            return self.typing_toggle(f);
        }
        if self.state.typing && ignored_while_typing(op) {
            return Vec::new();
        }
        if MENU_OPS.contains(&op) {
            return self.on_menu(op, f);
        }
        if op == RESET_OP {
            if let Some(desktops) = f.desktops.as_deref_mut() {
                desktops.layout_reset(out);
            }
            return Vec::new();
        }
        if RESIZE_OPS.contains(&op) {
            if let Some(pointer) = f.pointer.as_deref_mut() {
                pointer.on_op(op, out);
            }
            return Vec::new();
        }
        if crate::lua::view::SUPER_OPS.contains(&op) {
            if op == crate::lua::view::SUPER_OPS[2]
                && let Some(windows) = f.windows.as_deref_mut()
            {
                windows.mark(); // a drag inside a workspace sends Hyprland no event
            }
            if let Some(pointer) = f.pointer.as_deref_mut() {
                pointer.on_super(op, out);
            }
            return Vec::new();
        }
        if crate::lua::send::SEND_OPS.contains(&op) {
            if let (Some(sender), Some(name)) = (
                f.send.as_deref_mut(),
                self.state.names.get(self.state.selected),
            ) {
                sender.request(op, name); // the move itself runs off the loop
            }
            return Vec::new();
        }
        if op == ENVIRONMENT_OP {
            let env = environment::toggled(&self.state.environment.to_json());
            return self.set_environment(env);
        }
        if op == ENVIRONMENT_SET_OP {
            return match environment::validate(&payload) {
                Ok(env) => self.set_environment(env),
                Err(_) => Vec::new(),
            };
        }
        if op == IMMERSIVE_OP || op == IMMERSIVE_SET_OP {
            if let Some(immersive) = f.immersive.as_deref_mut() {
                immersive.on_message(message, out);
            }
            return Vec::new();
        }
        if op == PRIVACY_OP {
            if let Some(privacy) = f.privacy.as_deref_mut() {
                privacy.request(); // the compositor call runs off the loop
            }
            return Vec::new();
        }
        if let Some(&(_, turn)) = HELP_PAGE_OPS.iter().find(|(o, _)| *o == op) {
            return self.help_turn(turn);
        }
        if op == BINDS_PANEL_OP || op == BINDS_KEY_OP {
            if let Some(keys) = f.keys.as_deref_mut() {
                keys.on_message(message, self, out);
            }
            return Vec::new();
        }
        if !vs::USER_OPS.contains(&op) {
            return Vec::new();
        }
        self.user_op(op, f)
    }

    /// The `USER_OPS` (help, recenter, zoom, next / prev, spacing, prediction, mode_next).
    fn user_op(&mut self, op: &str, f: &mut Features<'_>) -> Vec<u8> {
        if op == "help" {
            return self.help_toggle(f);
        }
        if op == "recenter" {
            let id = self.take_id();
            let empty = serde_json::Map::new();
            return vs::frame(&self.run_id, &self.session, id, "recenter", empty).into_bytes();
        }
        // Maximizing collapses an open panel first (saved, keys released): pills and panels live in the overview.
        let mut out = if op == "zoom_in" && self.state.menu_open {
            self.on_menu("menu_toggle", f)
        } else {
            Vec::new()
        };
        if op == "mode_next" && !self.state.zoomed {
            self.tell(MODE_OVERVIEW_TOAST); // the overview is always anchored: nothing changes, say why
        }
        let before = self.state.view();
        // Zooming out: the overview pointer's `summon`.
        let direct =
            self.state.mode() != Mode::Anchor && self.state.zoomed && matches!(op, "next" | "prev");
        let target = self.state.apply(op).ok().flatten();
        if let (Some(target), Some(focus)) = (target, self.focus.clone()) {
            // Never block the loop on a compositor reply.
            self.threads.retain(|t| !t.is_finished());
            if let Ok(t) = std::thread::Builder::new()
                .name("xr-view-focus".into())
                .spawn(move || focus(&target))
            {
                self.threads.push(t);
            }
        }
        if matches!(op, "spacing_up" | "spacing_down" | "prediction") {
            self.save_settings(); // (the mode is not saved: maximize-follow)
        }
        if self.state.view() != before {
            let flags = if direct && self.state.selected != before.selected {
                Flags {
                    cut: Some(true),
                    ..Flags::default()
                }
            } else {
                Flags::default()
            };
            out.extend(self.view_frame(flags));
        }
        out.extend(self.release_placements()); // un-maximized by that op
        if self.state.selected != before.selected {
            out.extend(self.follow_focus());
        }
        out
    }

    fn on_stabilization(&mut self, payload: &serde_json::Value) -> Vec<u8> {
        let Some(map) = payload.as_object().filter(|m| m.len() == 1) else {
            return Vec::new();
        };
        let Some(value) = map.get("strength").and_then(serde_json::Value::as_i64) else {
            return Vec::new(); // `type(value) is not int` (a bool or a float is refused too)
        };
        if map["strength"].is_boolean() {
            return Vec::new();
        }
        let Ok(value) = vs::validate_stabilization(value) else {
            return Vec::new();
        };
        self.state.stabilization = value;
        self.save_settings();
        self.view_frame(Flags::default())
    }

    /// `_on_placement`: absolute placements from the panel, applied exactly as given, live, and saved; while
    /// maximized they wait for the overview (a toast says so). Invalid: ignored.
    fn on_placement(&mut self, payload: &serde_json::Value) -> Vec<u8> {
        let Some(desktops) = payload
            .as_object()
            .and_then(|p| p.get("desktops"))
            .and_then(serde_json::Value::as_array)
        else {
            return Vec::new();
        };
        let Ok(placements) = desktops
            .iter()
            .map(Placement::from_value)
            .collect::<Result<Vec<_>, _>>()
        else {
            return Vec::new();
        };
        if self.state.zoomed {
            let waiting = !self.state.held.is_empty();
            if self.state.hold_placements(&placements).is_ok()
                && !self.state.held.is_empty()
                && !waiting
            {
                self.tell(PLACE_HELD_TOAST);
            }
            return Vec::new();
        }
        if !self.state.set_placements(&placements).unwrap_or(false) {
            return Vec::new();
        }
        self.save_due = None; // this save also covers any pending pill-panel change
        self.save_placements();
        // An open glass panel shows the panel's values too.
        let mut out = self.placement_frame(false, false);
        if self.state.menu_open {
            out.extend(self.menu_frame());
        }
        out
    }

    /// `set_environment`: the background changes, applied live and saved with the view settings.
    fn set_environment(&mut self, env: Environment) -> Vec<u8> {
        if env == self.state.environment {
            return Vec::new();
        }
        self.state.environment = env;
        self.save_environment();
        let mut out = self.environment_frame(false);
        if self.state.menu_open {
            out.extend(self.menu_frame());
        }
        out
    }

    /// `_brightness`: the panel's Brightness row, applied live and saved after [`MENU_SAVE_DELAY`]; nothing with no
    /// background.
    fn brightness(&mut self, env: Environment) -> Vec<u8> {
        if !environment::on(&self.state.environment.to_json()) || env == self.state.environment {
            return Vec::new();
        }
        self.state.environment = env;
        self.environment_save_due = Some(self.now() + MENU_SAVE_DELAY);
        self.environment_frame(false)
    }

    fn help_toggle(&mut self, f: &mut Features<'_>) -> Vec<u8> {
        let mut out = Vec::new();
        if !self.help_visible {
            out.extend(self.close_keys(f)); // one centred card at a time
        }
        self.help_visible = !self.help_visible; // the renderer starts a reopened card on page 1
        let visible = self.help_visible;
        out.extend(self.help_frame(visible, 0));
        out
    }

    /// `help_turn`: the open help card flips a page; nothing while it is closed.
    fn help_turn(&mut self, turn: i64) -> Vec<u8> {
        if !self.help_visible {
            return Vec::new();
        }
        self.last_input = self.now(); // turning pages keeps the card up
        self.help_frame(true, turn)
    }

    /// `keys.close()` when the key bind panel is open (`keys_open`).
    fn close_keys(&mut self, f: &mut Features<'_>) -> Vec<u8> {
        let Some(keys) = f.keys.as_deref_mut().filter(|k| k.panel().open) else {
            return Vec::new();
        };
        let mut frames = Frames::default();
        keys.close(self, &mut frames);
        frames.bytes
    }

    /// `_typing_toggle`: Ctrl+Alt+T. On: an open panel collapses, the help card and the key bind panel close.
    fn typing_toggle(&mut self, f: &mut Features<'_>) -> Vec<u8> {
        let mut out = Vec::new();
        if !self.state.typing {
            if self.state.menu_open {
                out.extend(self.on_menu("menu_toggle", f)); // saves; releases the panel keys
            }
            if self.help_visible {
                out.extend(self.help_toggle(f));
            }
            out.extend(self.close_keys(f));
        }
        self.state.typing = !self.state.typing;
        self.binds_due = true;
        out.extend(self.view_frame(Flags::default())); // the renderer's pop-up shows the new mode
        out
    }

    /// `_zoom_out`: un-maximize to the overview, as Ctrl+Alt+Down does.
    fn zoom_out(&mut self) -> Vec<u8> {
        let _ = self.state.apply("zoom_out");
        let mut out = self.view_frame(Flags::default());
        out.extend(self.release_placements());
        out
    }

    /// `_release_placements`: back in the overview, the placements held while maximized apply now, gliding.
    fn release_placements(&mut self) -> Vec<u8> {
        if self.state.zoomed || self.state.held.is_empty() {
            return Vec::new();
        }
        let placements = self.state.wanted_placements();
        self.state.held.clear();
        if !self.state.set_placements(&placements).unwrap_or(false) {
            return Vec::new();
        }
        self.save_due = None; // this save also covers any pending pill-panel change
        self.save_placements();
        self.placement_frame(false, true)
    }

    /// `follow_focus`: the selection moved in the overview, so an open panel goes with it.
    fn follow_focus(&mut self) -> Vec<u8> {
        let s = &self.state;
        if !s.menu_open || s.zoomed || s.typing || self.video_active || s.selected == s.menu_desktop
        {
            return Vec::new();
        }
        self.flush();
        let selected = self.state.selected as i64;
        self.menu_update(None, |v| {
            let _ = v.state.open_menu(selected);
            false
        })
    }

    /// `_on_menu`: a panel key op. Ctrl+Alt+L while maximized goes back to the overview first.
    fn on_menu(&mut self, op: &str, f: &mut Features<'_>) -> Vec<u8> {
        if MENU_DESKTOP_OPS.contains(&op) {
            return Vec::new(); // the open panel's desktop row: the desktops' port (`desktop_action`)
        }
        let s = &self.state;
        if matches!(
            op,
            "menu_inc" | "menu_dec" | "menu_inc_big" | "menu_dec_big"
        ) && s.menu_open
            && s.menu_field == Field::Brightness
        {
            let step = if op.ends_with("_big") {
                BRIGHTNESS_STEPS.1
            } else {
                BRIGHTNESS_STEPS.0
            } * if op.starts_with("menu_inc") { 1 } else { -1 };
            let env = self.state.environment;
            let mut frames = Vec::new();
            let out = self.menu_update(Some(f), |v| {
                // (apply_menu closes an open list)
                frames = v.brightness(environment::brightened(
                    &env.to_json(),
                    env.brightness + step,
                ));
                v.state.apply_menu(op).unwrap_or(false)
            });
            frames.extend(out);
            return frames;
        }
        // Frames go out in the order they were built: each takes the next request id.
        let mut out = if op == "menu_toggle" && !self.state.menu_open && self.state.zoomed {
            self.zoom_out()
        } else {
            Vec::new()
        };
        // `menu_reset`: the overview pointer's `level` first.
        out.extend(self.menu_update(Some(f), |v| v.state.apply_menu(op).unwrap_or(false)));
        out
    }

    /// `_menu_update`: apply `change` (true if a placement changed) to the pills' state; a panel that opens closes
    /// the key bind panel (their keys would clash); the live placement frame, saved after [`MENU_SAVE_DELAY`]; a
    /// collapsing panel saved at once; the `placement_menu` frame if what it shows changed. (Bumping needs the overview
    /// pointer's layout: without it an edit applies as given.)
    fn menu_update(
        &mut self,
        f: Option<&mut Features<'_>>,
        change: impl FnOnce(&mut ViewControl) -> bool,
    ) -> Vec<u8> {
        let (before, was_open, placed) = (
            self.state.menu(),
            self.state.menu_open,
            self.state.placements.clone(),
        );
        let changed = change(self);
        let mut out = Vec::new();
        if self.state.menu_open
            && !was_open
            && let Some(f) = f
        {
            out.extend(self.close_keys(f));
        }
        if changed {
            if self.state.placements != placed {
                out.extend(self.placement_frame(false, false));
            }
            self.save_due = Some(self.now() + MENU_SAVE_DELAY);
        }
        if was_open && !self.state.menu_open {
            self.flush(); // collapsing saves at once
        }
        if was_open != self.state.menu_open {
            self.binds_due = true;
        }
        if self.state.menu() != before {
            out.extend(self.menu_frame());
        }
        out
    }
}

/// The view's frames for the key bind panel (`keybinds.BindControl`'s `control`).
impl crate::supervisor::keybinds::Host for ViewControl {
    fn now(&self) -> f64 {
        (self.clock)()
    }

    fn typing(&self) -> bool {
        self.state.typing
    }

    fn video_active(&self) -> bool {
        self.video_active
    }

    fn help_visible(&self) -> bool {
        self.help_visible
    }

    /// `control._help_toggle()` on an open card: closing it never touches the key bind panel.
    fn close_help(&mut self) -> Vec<u8> {
        if !self.help_visible {
            return Vec::new();
        }
        self.help_visible = false;
        self.help_frame(false, 0)
    }

    fn menu_open(&self) -> bool {
        self.state.menu_open
    }

    /// `control._on_menu('menu_toggle')` on an open panel: it collapses, saved at once.
    fn close_menu(&mut self) -> Vec<u8> {
        if !self.state.menu_open {
            return Vec::new();
        }
        self.menu_update(None, |v| v.state.apply_menu("menu_toggle").unwrap_or(false))
    }

    fn keyboard_owned(&self) -> bool {
        false // the options menu is not ported
    }

    fn run_id(&self) -> &str {
        &self.run_id
    }

    fn session(&self) -> &str {
        &self.session
    }

    fn next_request_id(&mut self) -> u64 {
        self.take_id()
    }

    fn set_last_input(&mut self, now: f64) {
        self.last_input = now;
    }

    fn binds_reinstalled(&mut self, typing: bool, menu_binds: bool, options_binds: bool) {
        self.typing_installed = typing;
        self.menu_binds_installed = menu_binds;
        self.options_binds_installed = options_binds;
    }
}

impl Drop for ViewControl {
    /// `join_focus`: the focus and toast threads are short; the run's end waits for them, bounded.
    fn drop(&mut self) {
        let deadline = Instant::now() + Duration::from_secs(2);
        for t in self.threads.drain(..) {
            while !t.is_finished() && Instant::now() < deadline {
                std::thread::sleep(Duration::from_millis(1));
            }
        }
    }
}

/// `time.monotonic()`.
fn monotonic() -> f64 {
    let mut ts = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    // SAFETY: a valid clock id and a valid out pointer.
    unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut ts) };
    ts.tv_sec as f64 + ts.tv_nsec as f64 / 1e9
}

/// A value as the control wire holds it, in the Python's key order.
fn wire<T: Serialize + ?Sized>(value: &T) -> xr_wire::json::Value {
    xr_wire::json::parse_strict(pyjson::compact(value).as_bytes())
        .unwrap_or(xr_wire::json::Value::Null)
}

/// A frame's payload as serde_json holds it.
fn plain(v: &xr_wire::json::Value) -> serde_json::Value {
    use xr_wire::json::Value as W;
    match v {
        W::Null => serde_json::Value::Null,
        W::Bool(b) => serde_json::Value::Bool(*b),
        W::Number(n) => serde_json::Value::Number(n.clone()),
        W::String(s) => serde_json::Value::String(s.clone()),
        W::Array(a) => serde_json::Value::Array(a.iter().map(plain).collect()),
        W::Object(o) => {
            serde_json::Value::Object(o.iter().map(|(k, v)| (k.clone(), plain(v))).collect())
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::view_controls::send::Sender;

    fn frame(op: &str) -> Frame {
        Frame {
            request_id: 2,
            run_id: "r1".into(),
            session: "s".into(),
            op: op.into(),
            payload: xr_wire::json::Value::Object(Vec::new()),
        }
    }

    fn none<'a>() -> Features<'a> {
        Features {
            pointer: None,
            send: None,
            privacy: None,
            desktops: None,
            immersive: None,
            windows: None,
            keys: None,
            binds: None,
        }
    }

    #[test]
    fn a_feature_that_is_not_there_takes_nothing_and_sends_nothing() {
        let mut view = ViewControl::with_names(vec!["XR-1".into()]);
        let mut out = Frames::default();
        for op in [
            "send_glasses",
            PRIVACY_OP,
            IMMERSIVE_OP,
            BINDS_PANEL_OP,
            RESET_OP,
            "resize_press",
        ] {
            view.on_message(&frame(op), &mut none(), &mut out);
        }
        assert!(out.bytes.is_empty() && out.fds.is_empty());
    }

    #[test]
    fn the_send_keys_reach_the_sender_with_the_selected_desktop() {
        let mut view = ViewControl::with_names(vec!["XR-1".into(), "XR-2".into()]);
        view.state.selected = 1;
        let mut sender = Sender::recording();
        let mut out = Frames::default();
        let mut features = Features {
            send: Some(&mut sender),
            ..none()
        };
        view.on_message(
            &frame(crate::lua::send::SEND_OPS[0]),
            &mut features,
            &mut out,
        );
        view.state.typing = true; // typing mode ignores them
        view.on_message(
            &frame(crate::lua::send::SEND_OPS[0]),
            &mut features,
            &mut out,
        );
        assert_eq!(
            sender.requests(),
            [(crate::lua::send::SEND_OPS[0].to_owned(), "XR-2".to_owned())]
        );
        assert!(out.bytes.is_empty());
    }

    #[test]
    fn maximizing_sends_the_view_and_focuses_the_selected_desktop() {
        let mut view = ViewControl::with_names(vec!["XR-1".into(), "XR-2".into()]);
        let focused = Arc::new(Mutex::new(Vec::new()));
        let seen = Arc::clone(&focused);
        view.set_focus(Some(Arc::new(move |name: &str| {
            seen.lock().unwrap().push(name.to_owned())
        })));
        let mut out = Frames::default();
        view.on_message(&frame("zoom_in"), &mut none(), &mut out);
        drop(view); // the focus thread joined
        let text = String::from_utf8(out.bytes).unwrap();
        assert!(text.starts_with(r#"{"schema":1,"request_id":2,"run_id":"r1","session":"s","op":"view","payload":{"zoomed":true,"selected":0,"#), "{text}");
        assert_eq!(*focused.lock().unwrap(), ["XR-1"]);
    }
}
