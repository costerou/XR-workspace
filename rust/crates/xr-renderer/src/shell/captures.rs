//! Managed-output capture and the composition of the captured desktops (the capture half of `spike/src/wayland.cpp`).
//!
//! Each `--capture` output (XR-n, never the presented one) is copied with ext-image-copy-capture-v1. Zero-copy GBM
//! dmabufs are tried first when the desktops are composed on the GPU; any reason they cannot be used is one of the
//! fixed strings the C++ logs, reported as that capture's `fallback`, and the capture uses two SHM slots instead.
//! The pure decisions (constraint batches, when to re-create buffers, the resize settle, the tiles, the CPU blit)
//! are in `xr_renderer::capture`; this module does what they decide.
//!
//! Composition: on the GPU (a `GlCompositor` on the compositor's linux-dmabuf main device) into a three-buffer GBM
//! swapchain shown through zwp_linux_dmabuf_v1, at the surface's fractional-scale size (decision g5: no wayland-egl);
//! without a GPU, side by side into SHM at the logical size. Either way a desktop keeps its last frame on screen
//! until a new one lands, never a blank tile.

use std::os::fd::{AsFd, AsRawFd};

use wayland_client::protocol::{wl_buffer::WlBuffer, wl_output::WlOutput, wl_registry::WlRegistry, wl_shm};
use wayland_client::{Connection, Dispatch, QueueHandle, WEnum, delegate_noop, event_created_child};
use wayland_protocols::ext::image_capture_source::v1::client::{
    ext_image_capture_source_v1::ExtImageCaptureSourceV1,
    ext_output_image_capture_source_manager_v1::ExtOutputImageCaptureSourceManagerV1,
};
use wayland_protocols::ext::image_copy_capture::v1::client::{
    ext_image_copy_capture_frame_v1::{self, ExtImageCopyCaptureFrameV1, FailureReason},
    ext_image_copy_capture_manager_v1::{ExtImageCopyCaptureManagerV1, Options},
    ext_image_copy_capture_session_v1::{self, ExtImageCopyCaptureSessionV1},
};
use wayland_protocols::wp::linux_dmabuf::zv1::client::{
    zwp_linux_buffer_params_v1::{self, Flags, ZwpLinuxBufferParamsV1},
    zwp_linux_dmabuf_feedback_v1::{self, ZwpLinuxDmabufFeedbackV1},
    zwp_linux_dmabuf_v1::ZwpLinuxDmabufV1,
};

use xr_renderer::capture::{
    Batch, Canvas, CaptureBuffers, DRM_XRGB8888, DmabufChoice, MOD_INVALID, MOD_LINEAR, ReadbackLimiter, ResizeSettle,
    SessionConstraints, TileSource, choose_dmabuf, compose_tiles, side_by_side,
};
use xr_renderer::desktops::MAX_DESKTOPS;
use xr_renderer::dmabuf::{Allocator, Dmabuf, dev_from_array, render_node};
use xr_renderer::egl::DmabufPlane;
use xr_renderer::gl::{GlCompositor, GpuImage};
use xr_wire::status::CapturePath;
use xr_renderer::options_geometry::json_string;
use xr_renderer::environment::{ENVIRONMENT_LOOP_S, EnvironmentFade};
use xr_renderer::hud::{BRIGHTNESS_DEFAULT, Background};
use xr_renderer::options_geometry::{maximize_geometry_json, options_step_scale, options_stepped};
use xr_renderer::view::{
    Canvas as ViewCanvas, Leaving, Size, SizeEase, TargetOptions, ViewAnimator, ZoomOptions, blended_layout_leaving,
    overview_geometry_json, overview_layout, zoomed_placements_with,
};
use xr_wire::control::{Environment, View};

use super::scene::Scene;
use super::{Error, MAX_BUFFERS, Presentation, ShmBuffer, State, monotonic_ms, monotonic_ms_f64};

/// A stopped session starts again this long after it stopped, at most this many times in a run.
const RESTART_MS: u64 = 100;
const MAX_RESTARTS: u32 = 16;
/// This many failed frames in a row end the run.
const MAX_FAILURE_RUN: u64 = 1000;
const DMABUF_SLOTS: usize = 3;
const SHM_SLOTS: usize = 2;
const SWAPCHAIN: usize = 3;
/// The layout's size of a desktop with no frame yet.
const PLACEHOLDER: Size = Size { w: 1920.0, h: 1080.0 };
/// Buffer ids at or above this are capture buffers: the compositor never releases them as surface buffers.
const CAPTURE_BUFFER_ID: u64 = u64::MAX;

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum Path {
    None,
    Shm,
    Dmabuf,
}

impl Path {
    fn name(self) -> &'static str {
        match self {
            Path::None => "none",
            Path::Shm => "shm",
            Path::Dmabuf => "dmabuf",
        }
    }
}

/// One capture dmabuf: the buffer object, its GL image and the wl_buffer the compositor copies into.
struct DmabufSlot {
    buf: Dmabuf,
    image: Option<GpuImage>,
    proxy: Option<WlBuffer>,
}

/// One swapchain buffer of the GPU composition.
struct SwapBuffer {
    _buf: Dmabuf,
    image: Option<GpuImage>,
    rb: glow::Renderbuffer,
    proxy: WlBuffer,
    id: u64,
    busy: bool,
}

struct Capture {
    name: String,
    /// Stable for the capture's life: its proxies' user data (its index moves when set_desktops adds or drops one).
    id: usize,
    output_global: Option<u32>,
    source: Option<ExtImageCaptureSourceV1>,
    session: Option<ExtImageCopyCaptureSessionV1>,
    frame: Option<ExtImageCopyCaptureFrameV1>,
    constraints: SessionConstraints,
    buffers: CaptureBuffers,
    resize: ResizeSettle,
    shape: SizeEase,
    shm: Vec<ShmBuffer>,
    /// CPU composition: the slot on screen while the buffers are re-created.
    held_shm: Option<ShmBuffer>,
    dbufs: Vec<DmabufSlot>,
    /// GPU composition: the dmabufs the desktop's texture still shows while new ones are made.
    held_dbufs: Vec<DmabufSlot>,
    capturing: Option<usize>,
    ready: Option<usize>,
    /// The dmabuf slot the texture is bound to.
    presented: Option<usize>,
    /// A ready SHM frame not uploaded yet.
    fresh: bool,
    stopped: bool,
    gone: bool,
    restarts: u32,
    restart_ms: Option<u64>,
    frames: u64,
    failures: u64,
    failure_run: u64,
    dmabuf_frames: u64,
    path: Path,
    batch: Option<Batch>,
    no_dmabuf: bool,
    dmabuf_failed: bool,
    pending: u32,
    /// Bumped whenever the dmabufs are dropped: late params events of older ones are ignored.
    generation: u64,
    choice: Option<DmabufChoice>,
    fallback: &'static str,
}

impl Capture {
    fn new(name: &str, id: usize) -> Self {
        Self {
            name: name.to_owned(),
            id,
            output_global: None,
            source: None,
            session: None,
            frame: None,
            constraints: SessionConstraints::default(),
            buffers: CaptureBuffers::default(),
            resize: ResizeSettle::default(),
            shape: SizeEase::default(),
            shm: Vec::new(),
            held_shm: None,
            dbufs: Vec::new(),
            held_dbufs: Vec::new(),
            capturing: None,
            ready: None,
            presented: None,
            fresh: false,
            stopped: false,
            gone: false,
            restarts: 0,
            restart_ms: None,
            frames: 0,
            failures: 0,
            failure_run: 0,
            dmabuf_frames: 0,
            path: Path::None,
            batch: None,
            no_dmabuf: false,
            dmabuf_failed: false,
            pending: 0,
            generation: 0,
            choice: None,
            fallback: "",
        }
    }

    /// The SHM slot whose frame is on screen (CPU composition).
    fn shown_shm(&self) -> Option<&ShmBuffer> {
        self.ready.and_then(|r| self.shm.get(r)).or(self.held_shm.as_ref())
    }

    fn layout_size(&self) -> Size {
        self.buffers.layout_size().unwrap_or(PLACEHOLDER)
    }
}

/// GPU composition: the compositor, its swapchain and what the report says about it.
struct Gpu {
    gl: GlCompositor,
    present: Allocator,
    modifier: u64,
    swapchain: Vec<SwapBuffer>,
    swap_size: (u32, u32),
    imported_frames: u64,
    uploaded_bytes: u64,
    /// The frames' GPU time (GL_EXT_disjoint_timer_query): the whole run (exit report) and since the last status
    /// sample.
    gpu_run: GpuTimes,
    gpu_window: GpuTimes,
}

/// GPU time per frame in 0.01 ms buckets up to 20 ms (more lands in the last bucket), and the exact maximum.
#[derive(Clone)]
struct GpuTimes {
    buckets: Vec<u64>,
    count: u64,
    max_ns: u64,
}

impl Default for GpuTimes {
    fn default() -> Self {
        Self { buckets: vec![0; Self::BUCKETS], count: 0, max_ns: 0 }
    }
}

impl GpuTimes {
    const BUCKET_MS: f64 = 0.01;
    const BUCKETS: usize = 2000;

    fn add(&mut self, ns: u64) {
        let i = ((ns as f64 / 1e6 / Self::BUCKET_MS) as usize).min(Self::BUCKETS - 1);
        self.buckets[i] += 1;
        self.count += 1;
        self.max_ns = self.max_ns.max(ns);
    }

    /// Nearest rank, the bucket's centre, never above the maximum.
    fn percentile_ms(&self, q: f64) -> f64 {
        let rank = ((q * self.count as f64).ceil() as u64).max(1);
        let mut seen = 0;
        let max_ms = self.max_ns as f64 / 1e6;
        for (i, &n) in self.buckets.iter().enumerate() {
            seen += n;
            if seen >= rank {
                return ((i as f64 + 0.5) * Self::BUCKET_MS).min(max_ms);
            }
        }
        max_ms
    }

    /// (p50, p95, max) in ms; None before the first timed frame.
    fn summary(&self) -> Option<(f64, f64, f64)> {
        (self.count != 0).then(|| (self.percentile_ms(0.50), self.percentile_ms(0.95), self.max_ns as f64 / 1e6))
    }
}

#[derive(Default)]
pub(super) struct CaptureState {
    names: Vec<String>,
    captures: Vec<Capture>,
    source_manager: Option<ExtOutputImageCaptureSourceManagerV1>,
    copy_manager: Option<ExtImageCopyCaptureManagerV1>,
    linux_dmabuf: Option<ZwpLinuxDmabufV1>,
    main_device: Option<u64>,
    feedback_done: bool,
    gpu: Option<Gpu>,
    /// Why the desktops are composed on the CPU (a fixed string).
    cpu_reason: &'static str,
    /// The capture dmabufs' device.
    allocator: Option<Allocator>,
    next_swap_id: u64,
    dirty: bool,
    geometry: String,
    geometry_sent: String,
    readback: ReadbackLimiter,
    /// The `view` op's zoom, selection and spacing (maximize is a zoomed view), eased.
    pub(super) view: ViewAnimator,
    /// The `environment` op: the background's fade, the one drawn (None's fade-out keeps the last), its brightness.
    environment: EnvironmentFade,
    background: Background,
    brightness: u16,
    /// The last frame drew the background: draw once more after it has faded out.
    environment_drawn: bool,
    maximize: String,
    maximize_sent: String,
    /// The next capture's id.
    next_id: usize,
}

/// Destroys dmabuf slots: their wl_buffers and GL images (the buffer objects go with them).
fn drop_dbufs(gpu: Option<&Gpu>, slots: &mut Vec<DmabufSlot>) {
    for slot in slots.drain(..) {
        if let Some(p) = slot.proxy {
            p.destroy();
        }
        if let (Some(image), Some(gpu)) = (slot.image, gpu) {
            gpu.gl.destroy_image(image);
        }
    }
}

fn drop_swapchain(gpu: &mut Gpu) {
    for b in gpu.swapchain.drain(..) {
        b.proxy.destroy();
        gpu.gl.delete_renderbuffer(b.rb);
        if let Some(image) = b.image {
            gpu.gl.destroy_image(image);
        }
    }
    gpu.swap_size = (0, 0);
}

fn plane(buf: &Dmabuf) -> DmabufPlane {
    DmabufPlane {
        fd: buf.fd.as_raw_fd(),
        width: buf.width,
        height: buf.height,
        fourcc: buf.fourcc,
        offset: buf.offset,
        stride: buf.stride,
        modifier: buf.modifier,
    }
}

impl CaptureState {
    /// The GPU compositor, once composition runs on the GPU (the VR video's GL context).
    pub(super) fn gl_mut(&mut self) -> Option<&mut GlCompositor> {
        self.gpu.as_mut().map(|g| &mut g.gl)
    }

    pub(super) fn new(names: &[String]) -> Self {
        Self {
            names: names.to_vec(),
            dirty: true,
            brightness: BRIGHTNESS_DEFAULT,
            maximize: String::from("{\"maximize_geometry\":null}"),
            ..Self::default()
        }
    }

    fn active(&self) -> bool {
        !self.names.is_empty()
    }

    fn new_capture(&mut self, name: &str) -> Capture {
        self.next_id += 1;
        Capture::new(name, self.next_id - 1)
    }

    /// The index of the capture with this id (None: it was dropped by set_desktops).
    fn index_of(&self, id: usize) -> Option<usize> {
        self.captures.iter().position(|c| c.id == id)
    }

    /// GPU composition is on (wayland.cpp's `impl_->gl`).
    pub(super) fn gpu(&self) -> bool {
        self.gpu.is_some()
    }

    /// The desktops' sizes as drawn (wayland.cpp capture_sizes): each one's eased shape, or its frame's size.
    pub(super) fn capture_sizes(&self) -> Vec<Size> {
        self.captures.iter().map(|c| c.shape.value().unwrap_or_else(|| c.layout_size())).collect()
    }

    /// What the layout is heading for (wayland.cpp settled_sizes): each desktop's frame size, the supervisor's hit
    /// testing.
    pub(super) fn settled_sizes(&self) -> Vec<Size> {
        self.captures.iter().map(Capture::layout_size).collect()
    }

    /// Registry: the capture globals, bound only when there is something to capture.
    pub(super) fn bind_global(
        &mut self,
        registry: &WlRegistry,
        name: u32,
        interface: &str,
        version: u32,
        qh: &QueueHandle<State>,
    ) {
        if !self.active() {
            return;
        }
        match interface {
            "ext_output_image_capture_source_manager_v1" if self.source_manager.is_none() => {
                self.source_manager = Some(registry.bind(name, 1, qh, ()));
            }
            "ext_image_copy_capture_manager_v1" if self.copy_manager.is_none() => {
                self.copy_manager = Some(registry.bind(name, 1, qh, ()));
            }
            "zwp_linux_dmabuf_v1" if version >= 4 && self.linux_dmabuf.is_none() => {
                self.linux_dmabuf = Some(registry.bind(name, 4, qh, ()));
            }
            _ => {}
        }
    }

    pub(super) fn output_removed(&mut self, global: u32) {
        for c in self.captures.iter_mut().filter(|c| c.output_global == Some(global)) {
            c.gone = true;
            c.stopped = true;
        }
    }

    pub(super) fn released(&mut self, id: u64) {
        if let Some(b) = self.gpu.as_mut().and_then(|g| g.swapchain.iter_mut().find(|b| b.id == id)) {
            b.busy = false;
        }
    }

    /// The surface's size or scale changed: draw again.
    pub(super) fn invalidate(&mut self) {
        self.dirty = true;
    }

    /// The desktops are composed on the GPU (head tracking needs it; wayland.cpp's `gl`).
    pub(super) fn gpu_composition(&self) -> bool {
        self.gpu.is_some()
    }

    /// The desktops the GPU composition draws, 0 without it (take_roll_geometry's `!gl || captures.empty()`).
    pub(super) fn gpu_desktops(&self) -> usize {
        self.gpu.as_ref().map_or(0, |_| self.captures.len())
    }

    /// The view's eased zoom now (0 overview, 1 zoomed in).
    pub(super) fn zoom(&self) -> f32 {
        self.view.zoom()
    }

    /// The captured desktops' names, capture order.
    pub(super) fn capture_names(&self) -> impl Iterator<Item = &str> {
        self.captures.iter().map(|c| c.name.as_str())
    }

    pub(super) fn close(&mut self) {
        let gpu = self.gpu.as_ref();
        for c in &mut self.captures {
            if let Some(f) = c.frame.take() {
                f.destroy();
            }
            if let Some(s) = c.session.take() {
                s.destroy();
            }
            if let Some(s) = c.source.take() {
                s.destroy();
            }
            c.shm.clear();
            c.held_shm = None;
            drop_dbufs(gpu, &mut c.dbufs);
            drop_dbufs(gpu, &mut c.held_dbufs);
        }
        if let Some(gpu) = &mut self.gpu {
            drop_swapchain(gpu);
        }
        if let Some(m) = self.copy_manager.take() {
            m.destroy();
        }
        if let Some(m) = self.source_manager.take() {
            m.destroy();
        }
        if let Some(d) = self.linux_dmabuf.take() {
            d.destroy();
        }
    }
}

impl State {
    /// The one output named `name` that is not the presented one.
    fn find_output(&self, name: &str) -> Option<(WlOutput, u32)> {
        let mut found =
            self.outputs.iter().enumerate().filter(|(i, o)| Some(*i) != self.selected && !o.name.is_empty() && o.name == name);
        let (_, o) = found.next()?;
        if found.next().is_some() {
            return None;
        }
        Some((o.proxy.clone(), o.global))
    }

    fn start_session(&mut self, i: usize, qh: &QueueHandle<State>) -> bool {
        let Some((output, global)) = self.find_output(&self.capture.captures[i].name) else {
            return false;
        };
        let cs = &mut self.capture;
        let (Some(sources), Some(copies)) = (&cs.source_manager, &cs.copy_manager) else {
            return false;
        };
        let source = sources.create_source(&output, qh, ());
        let session = copies.create_session(&source, Options::PaintCursors, qh, cs.captures[i].id);
        let c = &mut cs.captures[i];
        c.output_global = Some(global);
        c.source = Some(source);
        c.session = Some(session);
        c.stopped = false;
        c.gone = false;
        c.restart_ms = None;
        true
    }

    /// Keeps the frame on screen while the buffers go: the CPU composition keeps the SHM slot, the GPU one the
    /// dmabufs its texture shows (an uploaded SHM frame is in the texture already).
    fn hold_frame(&mut self, i: usize) {
        let cs = &mut self.capture;
        let c = &mut cs.captures[i];
        if cs.gpu.is_none() {
            if let Some(r) = c.ready.filter(|&r| r < c.shm.len()) {
                c.held_shm = Some(c.shm.swap_remove(r));
            }
        } else if c.presented.is_some() && !c.dbufs.is_empty() {
            drop_dbufs(cs.gpu.as_ref(), &mut c.held_dbufs);
            c.held_dbufs = std::mem::take(&mut c.dbufs);
        }
        c.ready = None;
        c.presented = None;
    }

    fn drop_buffers(&mut self, i: usize) {
        let cs = &mut self.capture;
        let c = &mut cs.captures[i];
        c.shm.clear();
        drop_dbufs(cs.gpu.as_ref(), &mut c.dbufs);
        c.capturing = None;
        c.path = Path::None;
        c.pending = 0;
        c.dmabuf_failed = false;
        c.generation += 1;
    }

    fn stop_session(&mut self, i: usize) {
        self.hold_frame(i);
        self.drop_buffers(i);
        let c = &mut self.capture.captures[i];
        if let Some(f) = c.frame.take() {
            f.destroy();
        }
        if let Some(s) = c.session.take() {
            s.destroy();
        }
        if let Some(s) = c.source.take() {
            s.destroy();
        }
        c.batch = None;
        c.constraints = SessionConstraints::default();
        c.buffers.restart();
    }

    fn capture_stopped(&mut self, i: usize) {
        let c = &mut self.capture.captures[i];
        c.stopped = true;
        if let Some(f) = c.frame.take() {
            f.destroy();
        }
        c.capturing = None;
        if c.gone {
            return;
        }
        c.restarts += 1;
        if c.restarts > MAX_RESTARTS {
            self.closed = true;
            return;
        }
        c.restart_ms = Some(monotonic_ms() + RESTART_MS);
    }

    /// Re-creates the buffers for the plan `CaptureBuffers::settle` recorded: dmabufs when they can be used,
    /// otherwise two SHM slots.
    fn allocate(&mut self, i: usize, qh: &QueueHandle<State>) {
        self.hold_frame(i);
        self.drop_buffers(i);
        if self.try_dmabuf(i, qh) {
            return;
        }
        self.drop_buffers(i);
        let Some(plan) = self.capture.captures[i].buffers.allocated() else {
            return;
        };
        let Ok(format) = wl_shm::Format::try_from(plan.format) else {
            self.closed = true;
            return;
        };
        let shm = self.shm.as_ref().expect("bound at open");
        let mut slots = Vec::with_capacity(SHM_SLOTS);
        for _ in 0..SHM_SLOTS {
            match ShmBuffer::create(
                shm,
                qh,
                c"xr-managed-capture",
                (plan.width, plan.height, plan.stride),
                format,
                CAPTURE_BUFFER_ID,
            ) {
                Ok(b) => slots.push(b),
                Err(_) => {
                    self.closed = true; // no memory for a capture: fail closed
                    return;
                }
            }
        }
        let c = &mut self.capture.captures[i];
        c.shm = slots;
        c.path = Path::Shm;
        self.capture_next(i, qh);
    }

    /// Zero-copy capture into three GBM dmabufs. False (and the reason as the capture's fallback) when they
    /// cannot be used.
    fn try_dmabuf(&mut self, i: usize, qh: &QueueHandle<State>) -> bool {
        let cs = &mut self.capture;
        let fail = |c: &mut Capture, why: &'static str| {
            c.no_dmabuf = true;
            c.fallback = why;
            false
        };
        if cs.captures[i].no_dmabuf {
            return false;
        }
        let Some(gpu) = &cs.gpu else {
            return fail(&mut cs.captures[i], "no gpu composition");
        };
        let Some(linux_dmabuf) = &cs.linux_dmabuf else {
            return fail(&mut cs.captures[i], "compositor lacks zwp_linux_dmabuf_v1");
        };
        let c = &mut cs.captures[i];
        let Some((device, offered, width, height)) = c
            .batch
            .as_ref()
            .and_then(|b| Some((b.device?, &b.dmabuf, b.width, b.height)))
            .filter(|(_, offered, _, _)| !offered.is_empty())
        else {
            return fail(c, "session offers no dmabuf");
        };
        if cs.allocator.as_ref().is_none_or(|a| a.dev() != device) {
            match Allocator::open(device) {
                Ok(a) => cs.allocator = Some(a),
                Err(why) => return fail(c, why),
            }
        }
        let allocator = cs.allocator.as_ref().expect("opened above");
        let Some(choice) = choose_dmabuf(offered, |f, m| gpu.gl.dmabuf_importable(f, m)) else {
            return fail(c, "no offered dmabuf format is importable");
        };
        let mut slots = Vec::with_capacity(DMABUF_SLOTS);
        for _ in 0..DMABUF_SLOTS {
            let buf = match allocator.allocate(width, height, choice.fourcc, choice.modifier, false) {
                Ok(b) => b,
                Err(why) => {
                    drop_dbufs(Some(gpu), &mut slots);
                    return fail(c, why);
                }
            };
            let Some(image) = gpu.gl.import_dmabuf(&plane(&buf)) else {
                drop_dbufs(Some(gpu), &mut slots);
                return fail(c, "egl dmabuf import failed");
            };
            slots.push(DmabufSlot { buf, image: Some(image), proxy: None });
        }
        for (j, slot) in slots.iter().enumerate() {
            let params = linux_dmabuf.create_params(qh, (c.id, c.generation, j));
            let b = &slot.buf;
            params.add(b.fd.as_fd(), 0, b.offset, b.stride, (b.modifier >> 32) as u32, b.modifier as u32);
            params.create(width as i32, height as i32, b.fourcc, Flags::empty());
            c.pending += 1;
        }
        c.dbufs = slots;
        c.choice = Some(choice);
        c.path = Path::Dmabuf;
        c.fallback = "";
        true
    }

    fn params_settled(&mut self, i: usize, qh: &QueueHandle<State>) {
        let c = &mut self.capture.captures[i];
        c.pending = c.pending.saturating_sub(1);
        if c.pending > 0 {
            return;
        }
        if c.dmabuf_failed {
            c.dmabuf_failed = false;
            c.no_dmabuf = true;
            c.fallback = "compositor rejected dmabuf buffer";
            self.allocate(i, qh);
        } else {
            self.capture_next(i, qh);
        }
    }

    /// Asks for the next frame into a slot nothing shows, unless one is in flight or the buffers are not ready.
    fn capture_next(&mut self, i: usize, qh: &QueueHandle<State>) {
        let c = &mut self.capture.captures[i];
        if c.stopped || c.frame.is_some() || !c.buffers.may_capture() {
            return;
        }
        let Some(session) = &c.session else {
            return;
        };
        let (slot, buffer, width, height) = match c.path {
            Path::Dmabuf => {
                if c.pending > 0 {
                    return;
                }
                let Some(j) = (0..c.dbufs.len())
                    .find(|&j| Some(j) != c.ready && Some(j) != c.presented && c.dbufs[j].proxy.is_some())
                else {
                    return;
                };
                let d = &c.dbufs[j];
                (j, d.proxy.clone().expect("checked"), d.buf.width, d.buf.height)
            }
            Path::Shm => {
                let j = if c.ready == Some(0) { 1 } else { 0 };
                let Some(b) = c.shm.get(j) else {
                    return;
                };
                (j, b.proxy.clone(), b.width, b.height)
            }
            Path::None => return,
        };
        let frame = session.create_frame(qh, c.id);
        frame.attach_buffer(&buffer);
        frame.damage_buffer(0, 0, width as i32, height as i32);
        frame.capture();
        c.frame = Some(frame);
        c.capturing = Some(slot);
    }

    fn frame_ready(&mut self, i: usize, qh: &QueueHandle<State>) {
        let gpu = self.capture.gpu.is_some();
        let c = &mut self.capture.captures[i];
        if let Some(f) = c.frame.take() {
            f.destroy();
        }
        c.ready = c.capturing.take();
        c.frames += 1;
        c.failure_run = 0;
        c.buffers.delivered();
        if c.path == Path::Dmabuf {
            c.dmabuf_frames += 1;
        }
        if !gpu {
            c.held_shm = None; // this frame replaces a held one
        }
        c.fresh = true;
        self.capture.dirty = true;
        self.capture_next(i, qh);
    }

    fn frame_failed(&mut self, i: usize, reason: WEnum<FailureReason>, qh: &QueueHandle<State>) {
        let c = &mut self.capture.captures[i];
        if let Some(f) = c.frame.take() {
            f.destroy();
        }
        c.capturing = None;
        c.failures += 1;
        c.failure_run += 1;
        if reason == WEnum::Value(FailureReason::Stopped) {
            self.capture_stopped(i);
            return;
        }
        if c.failure_run > MAX_FAILURE_RUN {
            self.closed = true;
            return;
        }
        if reason == WEnum::Value(FailureReason::BufferConstraints) {
            if c.buffers.reallocation_pending() {
                return; // new constraints are on their way
            }
            if c.path == Path::Dmabuf && c.dmabuf_frames == 0 {
                c.no_dmabuf = true;
                c.fallback = "compositor refused dmabuf capture";
                self.allocate(i, qh);
                return;
            }
            c.buffers.constraints_failed();
            return;
        }
        self.capture_next(i, qh);
    }

    /// Starts sessions that have none and restarts stopped ones when due.
    fn service_captures(&mut self, qh: &QueueHandle<State>, now: u64) {
        for i in 0..self.capture.captures.len() {
            let c = &self.capture.captures[i];
            let due = c.session.is_none() || (c.stopped && (c.gone || c.restart_ms.is_some_and(|t| now >= t)));
            if !due {
                continue;
            }
            if c.session.is_some() {
                self.stop_session(i);
            }
            if !self.start_session(i, qh) {
                self.capture.captures[i].stopped = true;
            }
        }
    }

    /// After each dispatch: re-create buffers whose events have settled, then keep every capture capturing.
    fn settle_captures(&mut self, qh: &QueueHandle<State>, now: u64) {
        for i in 0..self.capture.captures.len() {
            let c = &mut self.capture.captures[i];
            if c.stopped || c.session.is_none() || c.resize.hold(&c.buffers, now) {
                continue;
            }
            let in_flight = c.frame.is_some();
            if c.buffers.settle(in_flight) {
                self.allocate(i, qh);
            } else {
                self.capture_next(i, qh);
            }
        }
    }

    /// The capture half of set_desktops (wayland.cpp Impl::set_desktops): the captures not in `names` (a parsed,
    /// sorted capture list) stop and go, the missing ones go in at their place and the `renew` ones start again. Each
    /// index as it goes or comes is handed to the layout (its placements). Returns the names before, or None when
    /// captures cannot change (no capture run, not opened yet, or no capture globals).
    pub(super) fn change_captures(
        &mut self,
        names: &[String],
        renew: &[String],
        qh: &QueueHandle<State>,
    ) -> Option<Vec<String>> {
        let cs = &self.capture;
        if !cs.active() || cs.captures.is_empty() || cs.source_manager.is_none() || cs.copy_manager.is_none() {
            return None;
        }
        let was: Vec<String> = cs.captures.iter().map(|c| c.name.clone()).collect();
        for i in (0..self.capture.captures.len()).rev() {
            // back to front: indices below stay valid
            if names.contains(&self.capture.captures[i].name) {
                continue;
            }
            self.stop_session(i);
            let cs = &mut self.capture;
            let mut c = cs.captures.remove(i);
            drop_dbufs(cs.gpu.as_ref(), &mut c.held_dbufs);
            if let Some(gpu) = &mut cs.gpu {
                gpu.gl.remove_source(i);
            }
            self.features.layout.desktop_removed(i);
        }
        for (i, name) in names.iter().enumerate() {
            // both sorted: what is missing goes in at its place
            if self.capture.captures.get(i).is_some_and(|c| &c.name == name) {
                continue;
            }
            let c = self.capture.new_capture(name);
            self.capture.captures.insert(i, c);
            if let Some(gpu) = &mut self.capture.gpu {
                gpu.gl.insert_source(i);
            }
            self.features.layout.desktop_inserted(i);
        }
        for name in renew {
            for i in 0..self.capture.captures.len() {
                if &self.capture.captures[i].name != name {
                    continue;
                }
                if self.capture.captures[i].session.is_some() {
                    self.stop_session(i);
                }
                let c = &mut self.capture.captures[i];
                c.output_global = None;
                c.gone = false;
                c.stopped = false;
                c.restart_ms = None;
                c.restarts = 0;
            }
        }
        self.capture.names = names.to_vec();
        self.service_captures(qh, monotonic_ms());
        self.capture.dirty = true;
        Some(was)
    }

    /// The surface's buffer size: the fractional-scale size under a viewport (GPU composition only).
    /// The surface's buffer (wayland.cpp px_w x px_h): the whole layer surface, both eyes in side-by-side.
    fn scaled_size(&self) -> (u32, u32) {
        if self.viewport.is_some() && self.scale120 != 0 {
            let scale = |v: u32| ((u64::from(v) * u64::from(self.scale120) + 60) / 120) as u32;
            (scale(self.surface_w), scale(self.height))
        } else {
            (self.surface_w, self.height)
        }
    }
}

impl Presentation {
    /// After the first configure: the dmabuf main device, the GPU composition (or why not), the capture list.
    pub(super) fn open_captures(&mut self) -> Result<(), Error> {
        if !self.state.capture.active() {
            return Ok(());
        }
        if self.state.capture.source_manager.is_none() || self.state.capture.copy_manager.is_none() {
            return Err(Error::new("globals", "capture requires ext-image-copy-capture v1 and its output source"));
        }
        if let Some(d) = &self.state.capture.linux_dmabuf {
            let feedback = d.get_default_feedback(&self.qh, ());
            let deadline = std::time::Instant::now() + std::time::Duration::from_secs(2);
            while !self.state.capture.feedback_done && std::time::Instant::now() < deadline {
                if self.pump(50, None).is_err() {
                    break;
                }
            }
            feedback.destroy();
        }
        let n = self.state.capture.names.len();
        let s = &mut self.state;
        let names = s.capture.names.clone();
        let captures = names.iter().map(|n| s.capture.new_capture(n)).collect();
        s.capture.captures = captures;
        let (width, height) = (s.width, s.height);
        let gpu = match (&s.capture.linux_dmabuf, s.capture.main_device) {
            (None, _) => Err("compositor lacks zwp_linux_dmabuf_v1"),
            (Some(_), None) => Err("compositor sent no dmabuf main device"),
            (Some(_), Some(dev)) => render_node(dev)
                .ok_or("no render node for advertised dmabuf device")
                .and_then(|node| GlCompositor::create_on(Some(&node), width, height, n).map_err(|_| "egl unavailable"))
                .and_then(|gl| Allocator::open(dev).map(|a| (gl, a))),
        };
        match gpu {
            Ok((gl, present)) => {
                let modifier = if gl.dmabuf_importable(DRM_XRGB8888, MOD_LINEAR) { MOD_LINEAR } else { MOD_INVALID };
                s.capture.gpu = Some(Gpu {
                    gl,
                    present,
                    modifier,
                    swapchain: Vec::new(),
                    swap_size: (0, 0),
                    imported_frames: 0,
                    uploaded_bytes: 0,
                    gpu_run: GpuTimes::default(),
                    gpu_window: GpuTimes::default(),
                });
                s.features.cards.load_fonts(); // the cards' text font; the built-in pixel font is the reported fallback
            }
            Err(why) => s.capture.cpu_reason = why,
        }
        let qh = self.qh.clone();
        self.state.service_captures(&qh, monotonic_ms());
        Ok(())
    }

    pub fn capturing(&self) -> bool {
        self.state.capture.active()
    }

    /// Services the captures and, when the last frame was shown and something changed, draws and commits the
    /// desktops.
    pub fn draw_desktops(&mut self) -> Result<(), Error> {
        if !self.state.configured || self.state.closed {
            return Err(Error::new("state", "surface not configured"));
        }
        let qh = self.qh.clone();
        let now = monotonic_ms();
        self.state.service_captures(&qh, now);
        self.state.settle_captures(&qh, now);
        if self.state.closed {
            return Err(Error::new("capture", "a capture failed for good"));
        }
        let s = &self.state;
        let t = monotonic_ms_f64();
        let gl = s.capture.gpu.is_some();
        let env = &s.capture.environment;
        let animating = s.capture.captures.iter().any(|c| c.shape.animating())
            || (gl && (s.capture.view.animating() || env.animating(t) || s.features.animating(t)))
            || (s.capture.environment_drawn && !env.on());
        if s.frame.is_some() || !s.metrics.can_submit() || !(s.capture.dirty || animating) {
            return Ok(());
        }
        if self.state.capture.gpu.is_none() {
            return self.draw_cpu();
        }
        // D4: a head-tracked frame starts toward the compositor's next latch; pump wakes for it, the main loop calls
        // back in.
        if self.state.features.tracking.latch_wait() {
            return Ok(());
        }
        self.draw_gpu()
    }

    fn draw_cpu(&mut self) -> Result<(), Error> {
        let (width, height) = (self.state.width, self.state.height);
        self.state.buffers.retain(|b| b.busy || (b.width == width && b.height == height));
        let index = match self.state.buffers.iter().position(|b| !b.busy) {
            Some(i) => i,
            None if self.state.buffers.len() < MAX_BUFFERS => self.create_buffer()?,
            None => return Ok(()), // wait for a release; never reuse busy memory
        };
        let s = &mut self.state;
        let sizes: Vec<Size> = s.capture.captures.iter().map(Capture::layout_size).collect();
        let tiles = side_by_side(&sizes, width, height);
        let sources: Vec<TileSource> = s
            .capture
            .captures
            .iter()
            .map(|c| match c.shown_shm() {
                Some(b) => TileSource { pixels: Some(b.words()), width: b.width, height: b.height, stride_px: b.stride / 4 },
                None => TileSource { pixels: None, width: 0, height: 0, stride_px: 0 },
            })
            .collect();
        let buffer = &mut s.buffers[index];
        let mut canvas = Canvas { pixels: buffer.pixels(), width, height, stride_px: width };
        let mut columns = Vec::new();
        compose_tiles(&mut canvas, &sources, &tiles, 0, &mut columns);
        drop(sources);
        s.buffers[index].busy = true;
        let proxy = s.buffers[index].proxy.clone();
        s.capture.dirty = false;
        self.commit(&proxy, width, height)
    }

    fn build_swapchain(&mut self, size: (u32, u32)) -> Result<(), Error> {
        let fail = |why: &str| Error::new("gl", format!("swapchain: {why}"));
        let cs = &mut self.state.capture;
        let gpu = cs.gpu.as_mut().expect("gpu composition");
        drop_swapchain(gpu);
        let linux_dmabuf = cs.linux_dmabuf.as_ref().expect("gpu composition has linux-dmabuf");
        for _ in 0..SWAPCHAIN {
            let buf = gpu.present.allocate(size.0, size.1, DRM_XRGB8888, gpu.modifier, false).map_err(fail)?;
            let image = gpu.gl.import_dmabuf(&plane(&buf)).ok_or_else(|| fail("egl dmabuf import failed"))?;
            let Some(rb) = gpu.gl.image_renderbuffer(&image) else {
                gpu.gl.destroy_image(image);
                return Err(fail("renderbuffer from dmabuf failed"));
            };
            let id = cs.next_swap_id;
            cs.next_swap_id += 1;
            let params = linux_dmabuf.create_params(&self.qh, (usize::MAX, 0, 0));
            params.add(buf.fd.as_fd(), 0, buf.offset, buf.stride, (buf.modifier >> 32) as u32, buf.modifier as u32);
            // Swapchain ids count down from below the capture sentinel, apart from the SHM pattern buffers' ids.
            let proxy = params.create_immed(
                size.0 as i32,
                size.1 as i32,
                buf.fourcc,
                Flags::empty(),
                &self.qh,
                CAPTURE_BUFFER_ID - 1 - id,
            );
            params.destroy();
            gpu.swapchain.push(SwapBuffer {
                _buf: buf,
                image: Some(image),
                rb,
                proxy,
                id: CAPTURE_BUFFER_ID - 1 - id,
                busy: false,
            });
        }
        gpu.swap_size = size;
        Ok(())
    }

    fn draw_gpu(&mut self) -> Result<(), Error> {
        let (width, height) = (self.state.width, self.state.height);
        let size = self.state.scaled_size();
        if self.state.capture.gpu.as_ref().is_some_and(|g| g.swap_size != size) {
            self.build_swapchain(size)?;
        }
        let s = &mut self.state;
        let cs = &mut s.capture;
        let features = &mut s.features;
        let gpu = cs.gpu.as_mut().expect("gpu composition");
        let Some(k) = gpu.swapchain.iter().position(|b| !b.busy) else {
            return Ok(()); // all three on screen or queued: wait for a release
        };
        gpu.gl.resize(width, height, size.0, size.1);
        gpu.gl.begin_gpu_time(); // the frame's GPU work: uploads, depth, the draw and the copy into the swapchain
        let now = monotonic_ms_f64();
        let mut sizes = Vec::with_capacity(cs.captures.len());
        for (i, c) in cs.captures.iter_mut().enumerate() {
            match c.path {
                Path::Shm if c.fresh => {
                    if let Some(b) = c.ready.and_then(|r| c.shm.get(r)) {
                        if gpu.gl.upload(i, b.bytes(), b.width, b.height, b.stride) {
                            gpu.uploaded_bytes += b.bytes().len() as u64;
                        }
                    }
                    c.fresh = false;
                    drop_dbufs(Some(gpu), &mut c.held_dbufs);
                }
                Path::Dmabuf => {
                    if let Some(r) = c.ready.filter(|&r| c.presented != Some(r)) {
                        let d = &c.dbufs[r];
                        if let Some(image) = &d.image {
                            if gpu.gl.bind_source_image(i, image, d.buf.width, d.buf.height) {
                                gpu.imported_frames += 1;
                            }
                        }
                        c.presented = Some(r);
                        c.fresh = false;
                        drop_dbufs(Some(gpu), &mut c.held_dbufs);
                    }
                }
                _ => {}
            }
            let animate = c.shape.value().is_some();
            c.shape.advance(c.layout_size(), now, animate);
            sizes.push(c.shape.value().unwrap_or(PLACEHOLDER));
        }
        // Before the pose is read (wayland.cpp depth_tick), with this frame's pictures uploaded.
        let mut frames = [0u64; MAX_DESKTOPS];
        for (f, c) in frames.iter_mut().zip(&cs.captures) {
            *f = c.frames;
        }
        let shown = cs.captures.len().min(MAX_DESKTOPS);
        features.immersive.before_draw(&mut gpu.gl, &frames[..shown], cs.view.focus() as i32);
        // The view (wayland.cpp's render path): the blended layout of the eased zoom, and the zoomed desktop grown to
        // fill the view where it is. The features (scene.rs) then give the frame its placements, cards, glass and
        // pointer, and tracking the camera once the placements are in (Tracking::camera).
        let view = &mut cs.view;
        view.advance(now);
        let (w, h) = (width as f32, height as f32);
        let leaving = Leaving { zoom: view.left_zoom(), index: view.left() as usize };
        let tiles = blended_layout_leaving(&sizes, w, h, view.spacing(), view.zoom(), view.position(), leaving);
        let fov = features.tracking.fov(width, height);
        let mut scene = Scene::new(now, (width, height), fov, tiles);
        scene.overview = view.zoom() < 0.01 && !view.switching();
        scene.switching = view.switching();
        scene.overview_pan_landing = view.pan_overview() && view.pan_landing();
        scene.zoom = view.zoom();
        scene.focus = view.focus() as usize;
        scene.sizes.clone_from(&sizes);
        features.contribute(&mut scene);
        features.tracking.camera(&mut scene, view);
        let (half_w, half_h) = (scene.half_w, scene.half_h);
        let canvas = ViewCanvas::new(w, h, half_w, half_h);
        let overview = overview_layout(&sizes, w, h, view.spacing());
        let options = ZoomOptions { leaving, keep_size: features.tracking.keep_size() };
        let tiles = &scene.tiles;
        let mut placements =
            zoomed_placements_with(&canvas, &overview, tiles, &scene.own, view.zoom(), view.position(), options);
        // The Step-Back Band: the maximized desktop eases back about the view's centre; no tile, aim or saved placement
        // moves (wayland.cpp resolved_placements).
        let focus = view.focus() as usize;
        let mut unstepped = None;
        let e = features.handles.restore_step().value(now);
        if e > 0.0 && focus < placements.len() && focus < tiles.len() {
            unstepped = Some(placements[focus]);
            placements[focus] = options_stepped(placements[focus], options_step_scale(&tiles[focus]), e);
        }
        // The edge pan's dot (the hand's) in place of the free pointer: the real cursor is hidden and held.
        if let Some(dot) = features.layout.pan_dot(&canvas, &sizes, view, tiles, &placements, &features.roll) {
            scene.pointer = Some(dot);
        }
        cs.maximize = if view.zoom() >= 0.99 && !view.switching() && focus < tiles.len() {
            let placed = unstepped.as_ref().unwrap_or(&placements[focus]);
            maximize_geometry_json(&cs.captures[focus].name, &canvas, &tiles[focus], placed, &scene.head)
        } else {
            String::from("{\"maximize_geometry\":null}")
        };
        // The background behind everything, world-locked and level (identity untracked).
        let level = cs.environment.level(now);
        let seconds = ((now / 1000.0) % ENVIRONMENT_LOOP_S) as f32;
        gpu.gl.set_environment(level, cs.background, f32::from(cs.brightness), seconds, &scene.environment_world);
        cs.environment_drawn = level > 0.0;
        scene.apply(&mut gpu.gl);
        let video = features.video.draw(&mut gpu.gl, &scene);
        let drawn = match video {
            Some(drawn) => drawn, // a VR video replaces the desktops
            None => {
                let pan = view.pan_layers();
                let (highlight, alpha) = (view.focus() as i32, view.highlight_alpha());
                gpu.gl.draw_and_swap(tiles, &placements, highlight, alpha, &scene.head, half_w, half_h, pan.as_ref())
            }
        };
        if !drawn {
            return Err(Error::new("gl", "draw failed"));
        }
        features.menus.drawn(video.is_none());
        let target = &mut gpu.swapchain[k];
        if !gpu.gl.present_into(target.rb) {
            return Err(Error::new("gl", "present into the swapchain failed"));
        }
        gpu.gl.end_gpu_time();
        gpu.gl.finish();
        target.busy = true;
        if let Some(ns) = gpu.gl.take_gpu_ns() {
            gpu.gpu_run.add(ns);
            gpu.gpu_window.add(ns);
        }
        let proxy = target.proxy.clone();
        let settled = overview_layout(&cs.settled_sizes(), w, h, cs.view.target_spacing());
        cs.geometry = overview_geometry_json(&settled, width, height, half_w, half_h);
        cs.dirty = false;
        self.commit(&proxy, size.0, size.1)
    }

    /// The frames' GPU time since the last call, (p50, p95, max) ms, for the status datagram: None without
    /// GL_EXT_disjoint_timer_query or a timed frame since.
    pub(super) fn take_gpu_time(&mut self) -> Option<(f64, f64, f64)> {
        let gpu = self.state.capture.gpu.as_mut()?;
        let window = std::mem::take(&mut gpu.gpu_window);
        window.summary()
    }

    /// `capture_failure_run` and `capture_path` for the status datagram (None, None without captures).
    pub fn capture_status(&self) -> (Option<u64>, Option<CapturePath>) {
        let captures = &self.state.capture.captures;
        if !self.state.capture.active() {
            return (None, None);
        }
        let run = captures.iter().map(|c| c.failure_run).max().unwrap_or(0);
        let path = match captures.first().map(|c| c.path) {
            Some(first) if captures.iter().all(|c| c.frames > 0 && c.path == first) => match first {
                Path::Dmabuf => Some(CapturePath::Dmabuf),
                Path::Shm => Some(CapturePath::Shm),
                Path::None => None,
            },
            _ => None,
        };
        (Some(run), path)
    }

    /// The overview geometry line when it changed since it was last handed out.
    pub fn take_overview_geometry(&mut self) -> Option<String> {
        let cs = &mut self.state.capture;
        if cs.geometry.is_empty() || cs.geometry == cs.geometry_sent {
            return None;
        }
        cs.geometry_sent = cs.geometry.clone();
        Some(cs.geometry.clone())
    }

    pub fn resend_overview_geometry(&mut self) {
        self.state.capture.geometry_sent.clear();
    }

    /// The maximize geometry line (`null` unless a desktop is maximized) when it changed since it was last handed out.
    pub fn take_maximize_geometry(&mut self) -> Option<String> {
        let cs = &mut self.state.capture;
        if cs.gpu.is_none() || cs.maximize == cs.maximize_sent {
            return None;
        }
        cs.maximize_sent = cs.maximize.clone();
        Some(cs.maximize.clone())
    }

    pub fn resend_maximize_geometry(&mut self) {
        self.state.capture.maximize_sent.clear();
    }

    /// The `view` op (Presentation::set_view): the target zoom, selection (clamped to the desktops) and spacing.
    /// The viewing mode, pop-ups, prediction and stabilization have nothing to act on in this build.
    pub fn set_view(&mut self, v: &View) {
        let cs = &mut self.state.capture;
        let last = cs.captures.len().max(cs.names.len()).saturating_sub(1) as u32;
        let options = TargetOptions { center: v.center, cut: v.cut, pan: v.pan };
        cs.view.set_target_with(v.zoomed, v.selected.min(last), v.spacing, monotonic_ms_f64(), options);
        cs.dirty = true;
        self.view_tracking(v);
        self.view_popups(v);
    }

    /// The `environment` op (Presentation::set_environment): a background fades in, `none` fades the last one out.
    pub fn set_environment(&mut self, e: &Environment) {
        let cs = &mut self.state.capture;
        let chosen = match e.background {
            1 => Background::Grid,
            2 => Background::Smoke,
            _ => Background::None,
        };
        if chosen != Background::None {
            cs.background = chosen;
        }
        cs.brightness = e.brightness;
        cs.environment.set(chosen != Background::None, monotonic_ms_f64());
        cs.dirty = true;
    }

    /// The owner readback line (the supervisor's view of which desktops are on screen), rate limited.
    pub fn take_owner_readback(&mut self, now_ms: u64) -> Option<String> {
        let cs = &self.state.capture;
        if !cs.active() {
            return None;
        }
        // nlohmann's object keys are sorted: immersive, outputs, video; height, name, ready, width.
        let mut line = String::from("{\"owner_readback\":{\"immersive\":");
        match &cs.gpu {
            Some(g) => {
                let names: Vec<&str> = cs.captures.iter().map(|c| c.name.as_str()).collect();
                line.push_str(&self.state.features.immersive.readback(&g.gl.stereo_readback(), &names));
            }
            None => line.push_str("null"),
        }
        line.push_str(",\"outputs\":[");
        for (i, c) in cs.captures.iter().enumerate() {
            let (ready, w, h) = match &cs.gpu {
                Some(g) => match g.gl.source_texture(i) {
                    Some((_, w, h)) if w != 0 && h != 0 => (!c.stopped, w, h),
                    _ => (false, 0, 0),
                },
                None => match c.shown_shm() {
                    Some(b) => (!c.stopped, b.width, b.height),
                    None => (false, 0, 0),
                },
            };
            if i > 0 {
                line.push(',');
            }
            line.push_str(&format!("{{\"height\":{h},\"name\":"));
            json_string(&mut line, &c.name);
            line.push_str(&format!(",\"ready\":{ready},\"width\":{w}}}"));
        }
        line.push_str("],\"video\":");
        self.state.features.video.owner_readback(&mut line);
        line.push_str("}}");
        if !self.state.capture.readback.due(&line, now_ms) {
            return None;
        }
        self.state.capture.readback.sent(&line, now_ms);
        Some(line)
    }

    pub fn resend_owner_readback(&mut self) {
        self.state.capture.readback.resend();
    }

    /// The `managed_output_capture` exit report line.
    pub fn capture_report(&self) -> String {
        let s = &self.state;
        let cs = &s.capture;
        let mut out = String::from("{\"kind\":\"managed_output_capture\",\"outputs\":[");
        for (i, c) in cs.captures.iter().enumerate() {
            if i > 0 {
                out.push(',');
            }
            let plan = c.buffers.allocated().or(c.buffers.target());
            let (w, h, format) = plan.map_or((0, 0, 0), |p| (p.width, p.height, p.format));
            out.push_str("{\"name\":");
            json_string(&mut out, &c.name);
            out.push_str(&format!(
                ",\"frames\":{},\"failures\":{},\"width\":{w},\"height\":{h},\"format\":{format},\
                 \"constraint_batches\":{},\"reallocations\":{},\"resize_settles\":{},\"shape_eases\":{},\
                 \"path\":\"{}\",\"dmabuf_frames\":{}",
                c.frames,
                c.failures,
                c.buffers.batches(),
                c.buffers.reallocations(),
                c.resize.settles(),
                c.shape.eases(),
                c.path.name(),
                c.dmabuf_frames,
            ));
            if let (Path::Dmabuf, Some(choice)) = (c.path, c.choice) {
                out.push_str(&format!(",\"fourcc\":{},\"modifier\":{}", choice.fourcc, choice.modifier));
            }
            out.push_str(",\"fallback\":");
            json_string(&mut out, c.fallback);
            out.push_str(&format!(",\"stopped\":{}}}", c.stopped));
        }
        out.push(']');
        if cs.active() {
            s.features.report_before_surface(&mut out);
            let (bw, bh) = if cs.gpu.is_some() { s.scaled_size() } else { (s.width, s.height) };
            out.push_str(&format!(
                ",\"surface\":{{\"logical\":[{},{}],\"buffer\":[{bw},{bh}],\"scale120\":{},\"viewport\":{}}}",
                s.width,
                s.height,
                s.scale120,
                s.viewport.is_some()
            ));
            s.features.report_before_composition(&mut out, &cs.view);
            out.push_str(",\"composition\":");
            match &cs.gpu {
                Some(g) => {
                    let imported = cs.captures.iter().any(|c| c.path == Path::Dmabuf);
                    let (vendor, renderer, version) = g.gl.strings();
                    out.push_str(if imported {
                        "{\"path\":\"gles2-dmabuf-import\""
                    } else {
                        "{\"path\":\"gles2-shm-upload\""
                    });
                    out.push_str(&format!(
                        ",\"imported_frames\":{},\"uploaded_mib\":{:.1},\"vendor\":",
                        g.imported_frames,
                        g.uploaded_bytes as f64 / (1024.0 * 1024.0)
                    ));
                    json_string(&mut out, vendor);
                    out.push_str(",\"renderer\":");
                    json_string(&mut out, renderer);
                    out.push_str(",\"version\":");
                    json_string(&mut out, version);
                    // The frames' GPU time (GL_EXT_disjoint_timer_query); left out without the extension.
                    if g.gl.gpu_timer() {
                        let (p50, p95, max) = g.gpu_run.summary().unwrap_or_default();
                        out.push_str(&format!(
                            ",\"gpu_time\":{{\"frames\":{},\"p50_ms\":{p50:.3},\"p95_ms\":{p95:.3},\
                             \"max_ms\":{max:.3}}}",
                            g.gpu_run.count
                        ));
                    }
                    if let Some(stereo) = g.gl.stereo_report() {
                        out.push_str(",\"stereo\":");
                        out.push_str(&stereo);
                    }
                    out.push('}');
                }
                None => {
                    out.push_str("{\"path\":\"cpu-blit\",\"fallback\":");
                    json_string(&mut out, cs.cpu_reason);
                    out.push('}');
                }
            }
            s.features.report_after_composition(&mut out);
        }
        out.push('}');
        out
    }
}

impl Dispatch<ExtImageCopyCaptureSessionV1, usize> for State {
    fn event(
        s: &mut Self,
        _: &ExtImageCopyCaptureSessionV1,
        event: ext_image_copy_capture_session_v1::Event,
        &id: &usize,
        _: &Connection,
        qh: &QueueHandle<Self>,
    ) {
        use ext_image_copy_capture_session_v1::Event;
        let Some(i) = s.capture.index_of(id) else {
            return;
        };
        let c = &mut s.capture.captures[i];
        match event {
            Event::BufferSize { width, height } => c.constraints.size(width, height),
            Event::ShmFormat { format } => c.constraints.format(match format {
                WEnum::Value(f) => f.into(),
                WEnum::Unknown(u) => u,
            }),
            Event::DmabufDevice { device } => {
                if let Some(d) = dev_from_array(&device) {
                    c.constraints.dmabuf_device(d);
                }
            }
            Event::DmabufFormat { format, modifiers } => c.constraints.dmabuf_format(
                format,
                modifiers.chunks_exact(8).map(|b| u64::from_ne_bytes(b.try_into().expect("8 bytes"))).collect(),
            ),
            Event::Done => {
                let batch = c.constraints.done_all();
                match batch.shm {
                    None => {
                        c.stopped = true;
                        s.closed = true; // no usable SHM format or size: fail closed
                    }
                    Some(plan) => {
                        c.buffers.constraints(plan);
                        c.batch = Some(batch);
                        s.capture.dirty = true;
                        s.capture_next(i, qh);
                    }
                }
            }
            Event::Stopped => s.capture_stopped(i),
            _ => {}
        }
    }
}

impl Dispatch<ExtImageCopyCaptureFrameV1, usize> for State {
    fn event(
        s: &mut Self,
        _: &ExtImageCopyCaptureFrameV1,
        event: ext_image_copy_capture_frame_v1::Event,
        &id: &usize,
        _: &Connection,
        qh: &QueueHandle<Self>,
    ) {
        let Some(i) = s.capture.index_of(id) else {
            return;
        };
        match event {
            ext_image_copy_capture_frame_v1::Event::Ready => s.frame_ready(i, qh),
            ext_image_copy_capture_frame_v1::Event::Failed { reason } => s.frame_failed(i, reason, qh),
            _ => {}
        }
    }
}

impl Dispatch<ZwpLinuxBufferParamsV1, (usize, u64, usize)> for State {
    fn event(
        s: &mut Self,
        params: &ZwpLinuxBufferParamsV1,
        event: zwp_linux_buffer_params_v1::Event,
        &(id, generation, j): &(usize, u64, usize),
        _: &Connection,
        qh: &QueueHandle<Self>,
    ) {
        params.destroy();
        if id == usize::MAX {
            // A swapchain buffer (create_immed): one the compositor cannot import ends the run.
            if let zwp_linux_buffer_params_v1::Event::Failed = event {
                s.closed = true;
            }
            return;
        }
        let index = s.capture.index_of(id);
        let current = index.is_some_and(|i| s.capture.captures[i].generation == generation);
        let i = index.unwrap_or(usize::MAX);
        match event {
            zwp_linux_buffer_params_v1::Event::Created { buffer } => {
                if !current {
                    buffer.destroy();
                    return;
                }
                match s.capture.captures[i].dbufs.get_mut(j) {
                    Some(slot) => slot.proxy = Some(buffer),
                    None => buffer.destroy(),
                }
                s.params_settled(i, qh);
            }
            zwp_linux_buffer_params_v1::Event::Failed if current => {
                s.capture.captures[i].dmabuf_failed = true;
                s.params_settled(i, qh);
            }
            _ => {}
        }
    }

    event_created_child!(State, ZwpLinuxBufferParamsV1, [
        zwp_linux_buffer_params_v1::EVT_CREATED_OPCODE => (WlBuffer, CAPTURE_BUFFER_ID),
    ]);
}

impl Dispatch<ZwpLinuxDmabufFeedbackV1, ()> for State {
    fn event(
        s: &mut Self,
        _: &ZwpLinuxDmabufFeedbackV1,
        event: zwp_linux_dmabuf_feedback_v1::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        match event {
            zwp_linux_dmabuf_feedback_v1::Event::MainDevice { device } => s.capture.main_device = dev_from_array(&device),
            zwp_linux_dmabuf_feedback_v1::Event::Done => s.capture.feedback_done = true,
            _ => {} // the format table fd is closed as the event drops
        }
    }
}

delegate_noop!(State: ignore ZwpLinuxDmabufV1);
delegate_noop!(State: ExtOutputImageCaptureSourceManagerV1);
delegate_noop!(State: ExtImageCopyCaptureManagerV1);
delegate_noop!(State: ignore ExtImageCaptureSourceV1);


#[cfg(test)]
mod tests {
    use super::GpuTimes;

    #[test]
    fn gpu_times_give_nearest_rank_bucket_centres_and_the_exact_max() {
        let mut t = GpuTimes::default();
        assert_eq!(t.summary(), None);
        for us in 1..=100u64 {
            t.add(us * 10_000); // 0.01 .. 1.00 ms
        }
        t.add(30_000_000); // 30 ms: the last bucket, and the max
        let (p50, p95, max) = t.summary().unwrap();
        assert!((p50 - 0.515).abs() < 1e-9, "{p50}"); // rank 51 of 101: 0.51 ms, bucket centre
        assert!((p95 - 0.965).abs() < 1e-9, "{p95}"); // rank 96: 0.96 ms
        assert_eq!(max, 30.0);
        let mut one = GpuTimes::default();
        one.add(400_000);
        assert_eq!(one.summary(), Some((0.4, 0.4, 0.4))); // never above the max
    }
}
