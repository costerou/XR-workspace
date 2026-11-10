//! The Wayland shell: an output-bound overlay layer surface, paced by frame callbacks and measured
//! by presentation feedback, at the output's fractional scale through the viewporter.
//!
//! A port of the presentation-only path of `spike/src/wayland.cpp` (no capture, no GL). Globals:
//! wl_compositor v4, wl_shm v1, zwlr_layer_shell_v1 (v1..=4), named wl_output v4, and optional
//! wp_presentation v1, wp_viewporter v1 and wp_fractional_scale_manager_v1 v1. It never binds
//! wl_seat or any input global, and the surface takes no input. A required global or the selected
//! output going away, or the layer surface closing, ends the run.

use std::os::fd::{AsFd, BorrowedFd};
use std::os::unix::net::UnixStream;
use std::time::{Duration, Instant};

use rustix::event::{PollFd, PollFlags, Timespec, poll};
use rustix::fs::{MemfdFlags, ftruncate, memfd_create};
use rustix::mm::{MapFlags, ProtFlags, mmap, munmap};
use wayland_client::backend::WaylandError;
use wayland_client::protocol::{
    wl_buffer::{self, WlBuffer},
    wl_callback::{self, WlCallback},
    wl_compositor::WlCompositor,
    wl_output::{self, WlOutput},
    wl_region::WlRegion,
    wl_registry::{self, WlRegistry},
    wl_shm::{self, WlShm},
    wl_shm_pool::WlShmPool,
    wl_surface::WlSurface,
};
use wayland_client::{Connection, Dispatch, EventQueue, Proxy, QueueHandle, WEnum, delegate_noop};
use wayland_protocols::wp::fractional_scale::v1::client::{
    wp_fractional_scale_manager_v1::WpFractionalScaleManagerV1,
    wp_fractional_scale_v1::{self, WpFractionalScaleV1},
};
use wayland_protocols::wp::presentation_time::client::{
    wp_presentation::{self, WpPresentation},
    wp_presentation_feedback::{self, WpPresentationFeedback},
};
use wayland_protocols::wp::viewporter::client::{
    wp_viewport::WpViewport, wp_viewporter::WpViewporter,
};
use wayland_protocols_wlr::layer_shell::v1::client::{
    zwlr_layer_shell_v1::{self, ZwlrLayerShellV1},
    zwlr_layer_surface_v1::{self, ZwlrLayerSurfaceV1},
};

use crate::metrics::{Metrics, Presented};
use crate::pattern::{
    PatternCadence, canvas_width, layer_version, render_animated_pattern, valid_surface_size,
};

/// The layer surface's namespace, as the C++ renderer names it.
pub const NAMESPACE: &str = "xr-workspace-spike-test-pattern";
const MAX_BUFFERS: usize = 3;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Error {
    pub code: &'static str,
    pub detail: String,
}

impl Error {
    fn new(code: &'static str, detail: impl Into<String>) -> Self {
        Self {
            code,
            detail: detail.into(),
        }
    }
}

impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
        write!(f, "{}: {}", self.code, self.detail)
    }
}

/// Integer CLOCK_MONOTONIC milliseconds, as the C++ `monotonic_ms`.
pub fn monotonic_ms() -> u64 {
    let t = rustix::time::clock_gettime(rustix::time::ClockId::Monotonic);
    t.tv_sec as u64 * 1000 + t.tv_nsec as u64 / 1_000_000
}

/// CLOCK_MONOTONIC milliseconds with the fraction, for the status sample.
pub fn monotonic_ms_f64() -> f64 {
    let t = rustix::time::clock_gettime(rustix::time::ClockId::Monotonic);
    t.tv_sec as f64 * 1000.0 + t.tv_nsec as f64 / 1e6
}

struct Output {
    proxy: WlOutput,
    global: u32,
    name: String,
}

struct Feedback {
    proxy: WpPresentationFeedback,
    ticket: u64,
    selected_output: bool,
    other_output: bool,
}

/// One XRGB8888 shared-memory buffer. Its pixels are written only while the compositor does not hold it.
struct ShmBuffer {
    proxy: WlBuffer,
    id: u64,
    memory: *mut u32,
    bytes: usize,
    width: u32,
    height: u32,
    busy: bool,
}

impl ShmBuffer {
    fn pixels(&mut self) -> &mut [u32] {
        // SAFETY: `memory` maps `bytes` (width * height * 4) bytes for this buffer's whole life, and only
        // a buffer the compositor has released (not busy) is written.
        unsafe { std::slice::from_raw_parts_mut(self.memory, self.bytes / 4) }
    }
}

impl Drop for ShmBuffer {
    fn drop(&mut self) {
        self.proxy.destroy();
        // SAFETY: the mapping made in `create_buffer`, unmapped once here.
        let _ = unsafe { munmap(self.memory.cast(), self.bytes) };
    }
}

#[derive(Clone, Copy)]
enum CallbackKind {
    Frame,
    Sync,
}

#[derive(Default)]
struct State {
    compositor: Option<WlCompositor>,
    shm: Option<WlShm>,
    shell: Option<ZwlrLayerShellV1>,
    presentation: Option<WpPresentation>,
    viewporter: Option<WpViewporter>,
    fractional_manager: Option<WpFractionalScaleManagerV1>,
    required_globals: Vec<u32>,
    outputs: Vec<Output>,
    selected: Option<usize>,
    surface: Option<WlSurface>,
    layer: Option<ZwlrLayerSurfaceV1>,
    viewport: Option<WpViewport>,
    fractional: Option<WpFractionalScaleV1>,
    frame: Option<WlCallback>,
    synced: bool,
    feedbacks: [Option<Feedback>; Metrics::CAPACITY],
    buffers: Vec<ShmBuffer>,
    next_buffer_id: u64,
    metrics: Metrics,
    cadence: PatternCadence,
    /// The canvas (canvas_width): half the surface when side by side.
    width: u32,
    height: u32,
    /// The layer surface's own logical width.
    surface_w: u32,
    scale120: u32,
    configured: bool,
    closed: bool,
}

impl State {
    fn selected_output(&self) -> Option<&Output> {
        self.selected.map(|i| &self.outputs[i])
    }

    /// The viewport maps the buffer onto the logical size, so the compositor shows it 1:1. The SHM
    /// pattern stays at the logical size; only a GL path would render at `scaled_surface_size`.
    fn apply_scale(&mut self) {
        if self.width == 0 || self.height == 0 {
            return;
        }
        if let Some(viewport) = &self.viewport {
            viewport.set_destination(self.surface_w as i32, self.height as i32);
        }
        self.cadence.invalidate();
    }

    fn cancel_feedback(&mut self) {
        for f in &mut self.feedbacks {
            *f = None; // the proxy is dropped; its late events find no slot
        }
        self.metrics.abandon();
    }
}

impl Dispatch<WlRegistry, ()> for State {
    fn event(
        s: &mut Self,
        registry: &WlRegistry,
        event: wl_registry::Event,
        _: &(),
        _: &Connection,
        qh: &QueueHandle<Self>,
    ) {
        match event {
            wl_registry::Event::Global {
                name,
                interface,
                version,
            } => match interface.as_str() {
                "wl_compositor" if version >= 4 && s.compositor.is_none() => {
                    s.compositor = Some(registry.bind(name, 4, qh, ()));
                    s.required_globals.push(name);
                }
                "wl_shm" if s.shm.is_none() => {
                    s.shm = Some(registry.bind(name, 1, qh, ()));
                    s.required_globals.push(name);
                }
                "zwlr_layer_shell_v1" if version > 0 && s.shell.is_none() => {
                    s.shell = Some(registry.bind(name, layer_version(version), qh, ()));
                    s.required_globals.push(name);
                }
                "wp_presentation" if version > 0 && s.presentation.is_none() => {
                    s.metrics.bind(version); // v1 semantics, even when v2 is advertised
                    s.presentation =
                        Some(registry.bind(name, s.metrics.snapshot().protocol_version, qh, ()));
                    // Removal stops the run, never silently changes timing capability.
                    s.required_globals.push(name);
                }
                "wp_viewporter" if s.viewporter.is_none() => {
                    s.viewporter = Some(registry.bind(name, 1, qh, ()))
                }
                "wp_fractional_scale_manager_v1" if s.fractional_manager.is_none() => {
                    s.fractional_manager = Some(registry.bind(name, 1, qh, ()));
                }
                "wl_output" if version >= 4 => {
                    let proxy = registry.bind(name, 4, qh, name);
                    s.outputs.push(Output {
                        proxy,
                        global: name,
                        name: String::new(),
                    });
                }
                // Never wl_seat, virtual input or SDK globals.
                _ => {}
            },
            wl_registry::Event::GlobalRemove { name } => {
                if s.selected_output().is_some_and(|o| o.global == name)
                    || s.required_globals.contains(&name)
                {
                    s.closed = true;
                }
                for o in s.outputs.iter_mut().filter(|o| o.global == name) {
                    o.name.clear();
                }
            }
            _ => {}
        }
    }
}

impl Dispatch<WlOutput, u32> for State {
    fn event(
        s: &mut Self,
        _: &WlOutput,
        event: wl_output::Event,
        global: &u32,
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        if let wl_output::Event::Name { name } = event {
            if let Some(o) = s.outputs.iter_mut().find(|o| o.global == *global) {
                o.name = name;
            }
        }
    }
}

impl Dispatch<WpPresentation, ()> for State {
    fn event(
        s: &mut Self,
        _: &WpPresentation,
        event: wp_presentation::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        if let wp_presentation::Event::ClockId { clk_id } = event {
            s.metrics.clock(clk_id);
        }
    }
}

impl Dispatch<WpPresentationFeedback, usize> for State {
    fn event(
        s: &mut Self,
        proxy: &WpPresentationFeedback,
        event: wp_presentation_feedback::Event,
        slot: &usize,
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        let selected = s.selected_output().map(|o| o.proxy.clone());
        let Some(f) = s.feedbacks[*slot].as_mut().filter(|f| f.proxy == *proxy) else {
            return;
        };
        match event {
            wp_presentation_feedback::Event::SyncOutput { output } => {
                if selected.is_some_and(|o| o == output) {
                    f.selected_output = true;
                } else {
                    f.other_output = true;
                }
            }
            wp_presentation_feedback::Event::Presented {
                tv_sec_hi,
                tv_sec_lo,
                tv_nsec,
                refresh,
                seq_hi,
                seq_lo,
                flags,
            } => {
                let flags = match flags {
                    WEnum::Value(k) => k.bits(),
                    WEnum::Unknown(raw) => raw,
                };
                let (ticket, on_selected) = (f.ticket, f.selected_output && !f.other_output);
                s.feedbacks[*slot] = None;
                let p = Presented {
                    sec_hi: tv_sec_hi,
                    sec_lo: tv_sec_lo,
                    nsec: tv_nsec,
                    refresh,
                    seq_hi,
                    seq_lo,
                    flags,
                };
                s.metrics.presented(ticket, p, on_selected);
            }
            wp_presentation_feedback::Event::Discarded => {
                let ticket = f.ticket;
                s.feedbacks[*slot] = None;
                s.metrics.discarded(ticket);
            }
            _ => {}
        }
    }
}

impl Dispatch<ZwlrLayerSurfaceV1, ()> for State {
    fn event(
        s: &mut Self,
        layer: &ZwlrLayerSurfaceV1,
        event: zwlr_layer_surface_v1::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        match event {
            zwlr_layer_surface_v1::Event::Configure {
                serial,
                width,
                height,
            } => {
                layer.ack_configure(serial);
                if !valid_surface_size(width, height) {
                    s.closed = true;
                    return;
                }
                s.surface_w = width;
                s.width = canvas_width(width, height);
                s.height = height;
                s.configured = true;
                s.cadence.invalidate();
                s.apply_scale();
            }
            zwlr_layer_surface_v1::Event::Closed => s.closed = true,
            _ => {}
        }
    }
}

impl Dispatch<WpFractionalScaleV1, ()> for State {
    fn event(
        s: &mut Self,
        _: &WpFractionalScaleV1,
        event: wp_fractional_scale_v1::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        if let wp_fractional_scale_v1::Event::PreferredScale { scale } = event {
            if scale != s.scale120 {
                s.scale120 = scale;
                s.apply_scale();
            }
        }
    }
}

impl Dispatch<WlCallback, CallbackKind> for State {
    fn event(
        s: &mut Self,
        _: &WlCallback,
        event: wl_callback::Event,
        kind: &CallbackKind,
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        if let wl_callback::Event::Done { .. } = event {
            match kind {
                CallbackKind::Frame => {
                    s.frame = None;
                    s.cadence.frame_done();
                }
                CallbackKind::Sync => s.synced = true,
            }
        }
    }
}

impl Dispatch<WlBuffer, u64> for State {
    fn event(
        s: &mut Self,
        _: &WlBuffer,
        event: wl_buffer::Event,
        id: &u64,
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        if let wl_buffer::Event::Release = event {
            if let Some(b) = s.buffers.iter_mut().find(|b| b.id == *id) {
                b.busy = false;
            }
        }
    }
}

delegate_noop!(State: WlCompositor);
delegate_noop!(State: ignore WlShm);
delegate_noop!(State: WlShmPool);
delegate_noop!(State: ignore WlSurface);
delegate_noop!(State: WlRegion);
delegate_noop!(State: WpViewporter);
delegate_noop!(State: WpViewport);
delegate_noop!(State: WpFractionalScaleManagerV1);
delegate_noop!(State: ZwlrLayerShellV1);

/// The open presentation: the connection, its queue and what the events have told it.
pub struct Presentation {
    conn: Connection,
    queue: EventQueue<State>,
    qh: QueueHandle<State>,
    state: State,
}

impl Presentation {
    /// Connects over the inherited socket and maps the layer surface on the named output.
    pub fn open(socket: UnixStream, output_name: &str) -> Result<Self, Error> {
        if !xr_wire::control::is_token(output_name) {
            return Err(Error::new("output", "invalid output name"));
        }
        let conn = Connection::from_socket(socket)
            .map_err(|_| Error::new("connect", "Wayland connection failed"))?;
        let queue = conn.new_event_queue();
        let qh = queue.handle();
        conn.display().get_registry(&qh, ());
        let mut p = Self {
            conn,
            queue,
            qh,
            state: State::default(),
        };
        if !p.roundtrip()
            || !p.roundtrip()
            || p.state.compositor.is_none()
            || p.state.shm.is_none()
            || p.state.shell.is_none()
        {
            return Err(Error::new(
                "globals",
                "requires compositor v4, shm v1, layer-shell v1..5 and named output v4",
            ));
        }
        let mut found = p
            .state
            .outputs
            .iter()
            .enumerate()
            .filter(|(_, o)| o.name == output_name)
            .map(|(i, _)| i);
        p.state.selected = found.next();
        if found.next().is_some() {
            return Err(Error::new("output", "ambiguous output name"));
        }
        let Some(selected) = p.state.selected else {
            return Err(Error::new("output", "named output not advertised"));
        };
        let (qh, s) = (&p.qh, &mut p.state);
        let compositor = s.compositor.as_ref().expect("checked above");
        let surface = compositor.create_surface(qh, ());
        if let (Some(viewporter), Some(manager)) = (&s.viewporter, &s.fractional_manager) {
            // Native resolution; the scale arrives with or after the first configure.
            s.viewport = Some(viewporter.get_viewport(&surface, qh, ()));
            s.fractional = Some(manager.get_fractional_scale(&surface, qh, ()));
        }
        let layer = s.shell.as_ref().expect("checked above").get_layer_surface(
            &surface,
            Some(&s.outputs[selected].proxy),
            zwlr_layer_shell_v1::Layer::Overlay,
            NAMESPACE.into(),
            qh,
            (),
        );
        use zwlr_layer_surface_v1::Anchor;
        layer.set_anchor(Anchor::Top | Anchor::Bottom | Anchor::Left | Anchor::Right);
        layer.set_size(0, 0);
        // Presentation policy: no keyboard, cover the whole output (bars included), no input at all.
        layer.set_keyboard_interactivity(zwlr_layer_surface_v1::KeyboardInteractivity::None);
        layer.set_exclusive_zone(-1);
        let region = compositor.create_region(qh, ());
        surface.set_input_region(Some(&region));
        region.destroy();
        surface.commit();
        s.surface = Some(surface);
        s.layer = Some(layer);
        let deadline = Instant::now() + Duration::from_secs(2);
        while !p.state.configured && Instant::now() < deadline {
            if p.pump(50, None).is_err() {
                break;
            }
        }
        if !p.state.configured || p.state.closed {
            return Err(Error::new("configure", "configure failed or timed out"));
        }
        Ok(p)
    }

    /// From now on, a pattern that moves for `duration_ms` and then ends the run.
    pub fn animate_pattern(&mut self, duration_ms: u32) -> Result<(), Error> {
        if duration_ms == 0 || duration_ms > 60_000 || self.state.metrics.snapshot().submitted != 0
        {
            return Err(Error::new(
                "pattern",
                "animation requires configured surface, finite 1..60000 ms, before submission",
            ));
        }
        self.state.cadence = PatternCadence::new(duration_ms, monotonic_ms());
        Ok(())
    }

    pub fn finished(&self) -> bool {
        self.state.cadence.finished(monotonic_ms())
    }

    /// Waits up to `timeout_ms` for events and dispatches them. Returns whether `wake` may be readable.
    pub fn dispatch(&mut self, timeout_ms: i32, wake: Option<BorrowedFd>) -> Result<bool, Error> {
        let closed = || Error::new("dispatch", "closed, removed or disconnected");
        if !(0..=100).contains(&timeout_ms) {
            return Err(closed());
        }
        self.pump(timeout_ms, wake).map_err(|()| closed())
    }

    fn roundtrip(&mut self) -> bool {
        self.state.synced = false;
        let _callback = self.conn.display().sync(&self.qh, CallbackKind::Sync);
        let deadline = Instant::now() + Duration::from_secs(2);
        while !self.state.synced && Instant::now() < deadline {
            if self.pump(50, None).is_err() {
                break;
            }
        }
        self.state.synced
    }

    /// One wait: dispatch what is queued, flush, poll the display (and `wake`), read, dispatch.
    fn pump(&mut self, timeout_ms: i32, wake: Option<BorrowedFd>) -> Result<bool, ()> {
        if self.state.closed {
            return Err(());
        }
        let mut dispatched = 0;
        let guard = loop {
            if let Some(guard) = self.queue.prepare_read() {
                break guard;
            }
            dispatched += self
                .queue
                .dispatch_pending(&mut self.state)
                .map_err(|_| ())?;
        };
        let backlog = match self.conn.flush() {
            Ok(()) => false,
            Err(WaylandError::Io(e)) if e.kind() == std::io::ErrorKind::WouldBlock => true,
            Err(_) => return Err(()),
        };
        let display_fd = guard.connection_fd();
        let mut flags = PollFlags::IN;
        if backlog {
            flags |= PollFlags::OUT;
        }
        let mut fds = vec![PollFd::new(&display_fd, flags)];
        if let Some(wake) = &wake {
            fds.push(PollFd::new(wake, PollFlags::IN));
        }
        // Events already queued may allow a draw: look, do not wait.
        let wait = if dispatched > 0 { 0 } else { timeout_ms };
        let timeout = Timespec {
            tv_sec: i64::from(wait / 1000),
            tv_nsec: i64::from(wait % 1000) * 1_000_000,
        };
        match poll(&mut fds, Some(&timeout)) {
            Err(rustix::io::Errno::INTR) => return Ok(true),
            Err(_) => return Err(()),
            Ok(_) => {}
        }
        let display = fds[0].revents();
        let wake_ready = wake.is_none() || !fds.get(1).is_some_and(|f| f.revents().is_empty());
        drop(fds);
        if display.intersects(PollFlags::ERR | PollFlags::HUP | PollFlags::NVAL) {
            return Err(());
        }
        if display.contains(PollFlags::IN) {
            match guard.read() {
                Ok(_) => {}
                Err(WaylandError::Io(e)) if e.kind() == std::io::ErrorKind::WouldBlock => {}
                Err(_) => return Err(()),
            }
        } else {
            drop(guard); // cancels the read
        }
        if self.queue.dispatch_pending(&mut self.state).is_err() || self.state.closed {
            return Err(());
        }
        // Send what the callbacks queued now, not with the next draw; a full socket waits for the next flush.
        let _ = self.conn.flush();
        Ok(wake_ready)
    }

    fn create_buffer(&mut self) -> Result<usize, Error> {
        let s = &mut self.state;
        let (width, height) = (s.width, s.height);
        let bytes = width as usize * height as usize * 4;
        let fd = memfd_create(c"xr-test-pattern", MemfdFlags::CLOEXEC)
            .map_err(|_| Error::new("shm", "memfd failed"))?;
        ftruncate(&fd, bytes as u64).map_err(|_| Error::new("shm", "resize failed"))?;
        // SAFETY: a fresh shared mapping of the whole memfd; ShmBuffer owns and unmaps it.
        let memory = unsafe {
            mmap(
                std::ptr::null_mut(),
                bytes,
                ProtFlags::READ | ProtFlags::WRITE,
                MapFlags::SHARED,
                &fd,
                0,
            )
        }
        .map_err(|_| Error::new("shm", "map failed"))?;
        let shm = s.shm.as_ref().expect("bound at open");
        let pool = shm.create_pool(fd.as_fd(), bytes as i32, &self.qh, ());
        let id = s.next_buffer_id;
        s.next_buffer_id += 1;
        let proxy = pool.create_buffer(
            0,
            width as i32,
            height as i32,
            (width * 4) as i32,
            wl_shm::Format::Xrgb8888,
            &self.qh,
            id,
        );
        pool.destroy();
        s.buffers.push(ShmBuffer {
            proxy,
            id,
            memory: memory.cast(),
            bytes,
            width,
            height,
            busy: false,
        });
        Ok(s.buffers.len() - 1)
    }

    /// Draws and commits the next pattern frame when the cadence, a free buffer and the feedback
    /// window allow; otherwise does nothing.
    pub fn draw_test_pattern(&mut self) -> Result<(), Error> {
        if !self.state.configured || self.state.closed {
            return Err(Error::new("state", "surface not configured"));
        }
        let now = monotonic_ms();
        if !self
            .state
            .cadence
            .ready(now, true, self.state.metrics.can_submit())
        {
            return Ok(());
        }
        let (width, height) = (self.state.width, self.state.height);
        self.state
            .buffers
            .retain(|b| b.busy || (b.width == width && b.height == height));
        let index = match self.state.buffers.iter().position(|b| !b.busy) {
            Some(i) => i,
            None => {
                let room = self.state.buffers.len() < MAX_BUFFERS;
                if !self
                    .state
                    .cadence
                    .ready(now, room, self.state.metrics.can_submit())
                {
                    return Ok(()); // wait for a release; never reuse busy memory
                }
                self.create_buffer()?
            }
        };
        let phase = self.state.cadence.phase();
        let s = &mut self.state;
        render_animated_pattern(
            s.buffers[index].pixels(),
            width,
            height,
            width as usize,
            phase,
        );
        if s.cadence.finished(monotonic_ms()) {
            return Ok(());
        }
        let buffer = &mut s.buffers[index];
        buffer.busy = true;
        let surface = s.surface.as_ref().expect("mapped at open");
        surface.attach(Some(&buffer.proxy), 0, 0);
        surface.damage_buffer(0, 0, width as i32, height as i32);
        s.frame = Some(surface.frame(&self.qh, CallbackKind::Frame));
        // Only this commit is counted; a full feedback window held the frame back above.
        if let Some(presentation) = &s.presentation {
            let slot = s
                .feedbacks
                .iter()
                .position(Option::is_none)
                .ok_or_else(|| Error::new("feedback", "feedback capacity invariant failed"))?;
            let ticket = s
                .metrics
                .submit()
                .ok_or_else(|| Error::new("feedback", "feedback capacity invariant failed"))?;
            let proxy = presentation.feedback(surface, &self.qh, slot);
            s.feedbacks[slot] = Some(Feedback {
                proxy,
                ticket,
                selected_output: false,
                other_output: false,
            });
        } else {
            s.metrics.submit();
        }
        surface.commit();
        s.cadence.committed();
        match self.conn.flush() {
            Ok(()) => Ok(()),
            Err(WaylandError::Io(e)) if e.kind() == std::io::ErrorKind::WouldBlock => Ok(()),
            Err(_) => Err(Error::new("flush", "connection failed")),
        }
    }

    pub fn status_json(&self, now_ms: f64) -> String {
        self.state.metrics.status_json(now_ms)
    }

    /// Ends the presentation. Feedback still in flight becomes abandoned, never an invented discard.
    pub fn close(mut self) -> Metrics {
        let s = &mut self.state;
        s.cadence.stop();
        s.cancel_feedback();
        let metrics = s.metrics.clone();
        if let Some(p) = s.presentation.take() {
            p.destroy();
        }
        if let Some(f) = s.fractional.take() {
            f.destroy();
        }
        if let Some(v) = s.viewport.take() {
            v.destroy();
        }
        if let Some(m) = s.fractional_manager.take() {
            m.destroy();
        }
        if let Some(v) = s.viewporter.take() {
            v.destroy();
        }
        s.frame = None;
        if let Some(l) = s.layer.take() {
            l.destroy();
        }
        if let Some(surface) = s.surface.take() {
            surface.destroy();
        }
        s.buffers.clear();
        for o in s.outputs.drain(..) {
            o.proxy.release();
        }
        if let Some(shell) = s.shell.take() {
            if shell.version() >= 3 {
                shell.destroy();
            }
        }
        let _ = self.conn.flush();
        metrics
    }
}
