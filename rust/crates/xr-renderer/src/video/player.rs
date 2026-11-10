//! VR video decode (docs/vr-video.md; port of spike/src/video_player.cpp): libmpv renders each decoded frame into a GL
//! texture the projection pass samples. mpv decodes on the GPU behind the current EGL context where it can (VA-API
//! surfaces imported as dmabufs with no CPU copy; vaapi-copy on that GPU, then software, as fallbacks). Each frame goes
//! on the display frame nearest the time mpv wants it on screen (FramePacer), not as soon as mpv hands it over.
//!
//! Everything runs on the renderer thread with the GL context current. Only asynchronous mpv commands and
//! non-blocking event polls are used, so drawing never waits on the mpv core. mpv is locked down: no config files,
//! scripts, ytdl, OSC, key bindings, sibling subtitle/audio files, shader or ICC cache (no disk writes), and nothing but
//! the inherited file descriptor is ever opened.

use std::ffi::{CString, c_char, c_int, c_void};
use std::os::fd::{AsRawFd, FromRawFd, IntoRawFd, OwnedFd};
use std::ptr::{self, NonNull};
use std::rc::Rc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Instant;

use glow::HasContext;

use super::math::{FramePacer, State, video_texture_levels, video_texture_size};
use super::mpv::*;
use super::readback::Readback;
use super::sphere::texture_filtering;
use crate::egl;

/// Closing waits at most this long for mpv to stop (longer than mpv's own 200 ms wait for a frame to be drawn).
const QUIESCE_NS: i64 = 300_000_000;
/// A reopen loads the next file after this even if mpv never reports that the previous one has stopped.
const RESTART_NS: i64 = 2_000_000_000;
/// mpv 0.41's libmpv GL path keeps one GL fence per rendered frame until the render context is freed (~175 bytes
/// each). A player is reused for at most about an hour of 60 fps frames, so that growth stays what one long file had.
const REUSE_MAX_FRAMES: u64 = 216_000;
const GL_RGBA8: c_int = 0x8058;

// Observed properties and async requests (reply_userdata identifies them).
const PAUSE: u64 = 1;
const TIME: u64 = 2;
const DURATION: u64 = 3;
const EOF: u64 = 4;
const WIDTH: u64 = 5;
const HEIGHT: u64 = 6;
const HWDEC: u64 = 7;
const DROPS: u64 = 8;
const QUALITY: u64 = 9;
const DEBAND: u64 = 10;
const PAUSE_COMMAND: u64 = 11;
const SEEK_COMMAND: u64 = 12;

/// Printable ASCII without quotes or backslashes (safe inside a JSON string), at most `limit`, trailing spaces cut.
fn ascii(s: &[u8], limit: usize) -> String {
    let mut out: String = s
        .iter()
        .filter(|&&c| (0x20..0x7f).contains(&c) && c != b'"' && c != b'\\')
        .take(limit)
        .map(|&c| c as char)
        .collect();
    out.truncate(out.trim_end_matches(' ').len());
    out
}

/// mpv's DRM-plane overlay interop always fails without a DRM display (fd -1), with lines not about the video.
fn interop_noise(prefix: Option<&[u8]>) -> bool {
    prefix.is_some_and(|p| p.starts_with(b"libmpv_render/drmprime-overlay"))
}

unsafe extern "C" fn get_proc_address(_: *mut c_void, name: *const c_char) -> *mut c_void {
    // SAFETY: mpv passes a NUL-terminated function name.
    egl::proc_address(unsafe { std::ffi::CStr::from_ptr(name) })
}

unsafe extern "C" fn on_update(data: *mut c_void) {
    // SAFETY: data is the player's boxed AtomicBool, alive until the render context is freed (Drop).
    unsafe { &*data.cast::<AtomicBool>() }.store(true, Ordering::Relaxed);
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Options {
    pub audio: bool,
    pub hwdec: bool,
    /// The compositor's wl_display: VA-API there when the EGL context does not name its render node.
    pub display: Option<NonNull<c_void>>,
}

impl Default for Options {
    fn default() -> Self {
        Self {
            audio: true,
            hwdec: true,
            display: None,
        }
    }
}

pub struct Player {
    gl: Rc<glow::Context>,
    mpv: *mut mpv_handle,
    render: *mut mpv_render_context,
    options: Options,
    update_pending: Box<AtomicBool>,
    fbo: Option<glow::Framebuffer>,
    tex: Option<glow::Texture>,
    tex_w: u32,
    tex_h: u32,
    video_w: u32,
    video_h: u32,
    readback: Readback,
    loaded: bool,
    paused: bool,
    eof: bool,
    failed: bool,
    rendered: bool,
    idle: bool,
    position: f64,
    duration: f64,
    why: String,
    hwdec: String,
    last_log: String,
    render_node: Option<String>,
    frames: u64,
    reallocations: u64,
    gl_errors: u64,
    reopens: u64,
    discarded: u64,
    // GL limits, read once: a glGet per display frame is a driver round trip (a full sync with Mesa's glthread).
    max_texture: i32,
    max_attribs: i32,
    levels: u32,
    anisotropy: f32,
    // VA-API zero copy on the context's own GPU (mpv's DRM display parameters; mpv keeps a pointer to them).
    render_fd: Option<OwnedFd>,
    drm: Box<mpv_opengl_drm_params_v2>,
    // reopen(): the next file, kept until mpv has stopped the previous one and its video output is gone. mpv numbers
    // playlist entries 1, 2, 3... per loadfile, so `requested` is the current file's id; older entries are ignored.
    next_fd: Option<OwnedFd>,
    restart_since_ns: i64,
    requested: i64,
    started: bool,
    old_ended: bool,
    pause_next: bool,
    pacer: FramePacer,
    render_sum_ms: f64,
    render_max_ms: f64,
    drops: i64,
    quality_set: u64,
    quality_errors: u64,
}

impl Player {
    /// Play the file behind `fd` (a readable regular file or pipe). The GL context must be current now and whenever
    /// the player is used or dropped. Err with the reason; `fd` is closed either way.
    pub fn open(gl: Rc<glow::Context>, fd: OwnedFd, options: Options) -> Result<Self, String> {
        // SAFETY: plain libmpv constructor.
        let mpv = unsafe { mpv_create() };
        if mpv.is_null() {
            return Err("mpv_create failed".into());
        }
        // SAFETY: GL queries on the current context.
        let (max_texture, max_attribs) = unsafe {
            (
                gl.get_parameter_i32(glow::MAX_TEXTURE_SIZE),
                gl.get_parameter_i32(glow::MAX_VERTEX_ATTRIBS),
            )
        };
        let mut s = Self {
            gl,
            mpv,
            render: ptr::null_mut(),
            options,
            update_pending: Box::new(AtomicBool::new(false)),
            fbo: None,
            tex: None,
            tex_w: 0,
            tex_h: 0,
            video_w: 0,
            video_h: 0,
            readback: Readback::default(),
            loaded: false,
            paused: false,
            eof: false,
            failed: false,
            rendered: false,
            idle: false,
            position: 0.0,
            duration: 0.0,
            why: String::new(),
            hwdec: "unknown".into(),
            last_log: String::new(),
            render_node: None,
            frames: 0,
            reallocations: 0,
            gl_errors: 0,
            reopens: 0,
            discarded: 0,
            max_texture,
            max_attribs: max_attribs.clamp(0, 64),
            levels: 0,
            anisotropy: 1.0,
            render_fd: None,
            drm: Box::new(mpv_opengl_drm_params_v2 {
                fd: -1,
                crtc_id: -1,
                connector_id: -1,
                atomic_request_ptr: ptr::null_mut(),
                render_fd: -1,
            }),
            next_fd: None,
            restart_since_ns: 0,
            requested: 1,
            started: false,
            old_ended: false,
            pause_next: false,
            pacer: FramePacer::default(),
            render_sum_ms: 0.0,
            render_max_ms: 0.0,
            drops: 0,
            quality_set: 0,
            quality_errors: 0,
        };
        // Hardware decode on the GPU that draws the video: mpv's vaapi interop imports each decoded surface into this
        // context (zero copy). Without a render node it falls back to the Wayland display, as before.
        if options.hwdec {
            s.render_node = egl::render_node(&s.gl);
            s.render_fd = s.render_node.as_deref().and_then(|node| {
                let path = CString::new(node).ok()?;
                // SAFETY: path is NUL-terminated; a non-negative result is a fresh descriptor we own.
                let fd = unsafe {
                    libc::open(
                        path.as_ptr(),
                        libc::O_RDWR | libc::O_CLOEXEC | libc::O_NOCTTY,
                    )
                };
                // SAFETY: fd is fresh and owned by nothing else.
                (fd >= 0).then(|| unsafe { OwnedFd::from_raw_fd(fd) })
            });
            if s.render_fd.is_none() {
                s.render_node = None;
            }
            s.drm.render_fd = s.render_fd.as_ref().map_or(-1, |f| f.as_raw_fd());
        }
        let hwdec = if options.hwdec {
            "vaapi,vaapi-copy,auto-safe"
        } else {
            "no"
        };
        let ao = if options.audio {
            "pipewire,pulse,alsa"
        } else {
            "null"
        };
        let node = s.render_node.clone();
        let opts: [(&str, Option<&str>, bool); 40] = [
            ("config", Some("no"), true),
            ("load-scripts", Some("no"), true),
            ("ytdl", Some("no"), true),
            ("osc", Some("no"), false),
            ("input-default-bindings", Some("no"), true),
            ("input-builtin-bindings", Some("no"), false),
            ("input-vo-keyboard", Some("no"), true),
            ("terminal", Some("no"), true),
            ("vo", Some("libmpv"), true),
            ("idle", Some("yes"), true),
            ("keep-open", Some("yes"), true),
            ("force-window", Some("no"), true),
            ("sub-auto", Some("no"), true),
            ("audio-file-auto", Some("no"), true),
            ("cover-art-auto", Some("no"), false),
            ("autoload-files", Some("no"), false),
            ("save-position-on-quit", Some("no"), true),
            ("resume-playback", Some("no"), true),
            ("load-stats-overlay", Some("no"), false),
            ("load-osd-console", Some("no"), false),
            ("load-console", Some("no"), false),
            ("load-auto-profiles", Some("no"), false),
            ("load-select", Some("no"), false),
            ("load-commands", Some("no"), false),
            ("load-context-menu", Some("no"), false),
            ("load-positioning", Some("no"), false),
            ("osd-level", Some("0"), true),
            ("osd-bar", Some("no"), true),
            ("gpu-shader-cache", Some("no"), false),
            ("icc-cache", Some("no"), false),
            ("keepaspect", Some("no"), true),
            ("audio-display", Some("no"), true),
            ("audio-client-name", Some("viture-workspace"), false),
            ("hwdec", Some(hwdec), true),
            ("ao", Some(ao), true),
            (
                "demuxer-lavf-o",
                Some("protocol_whitelist=[file,fd]"),
                false,
            ),
            // mpv 0.40+ would otherwise open its own Wayland connection for the clipboard and bind wl_seat.
            ("clipboard-backends", Some(""), false),
            ("clipboard-monitor", Some("no"), false),
            ("input-ipc-server", Some(""), false),
            // vaapi-copy (the fallback when the interop fails) decodes on the same GPU instead of renderD128.
            ("vaapi-device", node.as_deref(), false),
        ];
        for (name, value, required) in opts {
            let Some(value) = value else { continue };
            let (n, v) = (
                CString::new(name).unwrap(),
                CString::new(value).map_err(|_| format!("mpv option {name}"))?,
            );
            // SAFETY: both strings are NUL-terminated; mpv copies them.
            if unsafe { mpv_set_option_string(s.mpv, n.as_ptr(), v.as_ptr()) } < 0 && required {
                return Err(format!("mpv option {name}"));
            }
        }
        let level = CString::new(std::env::var("XR_MPV_LOG").unwrap_or_else(|_| "error".into()))
            .unwrap_or_default();
        // SAFETY: valid handle and NUL-terminated level.
        if unsafe {
            mpv_request_log_messages(s.mpv, level.as_ptr());
            mpv_initialize(s.mpv)
        } < 0
        {
            return Err("mpv_initialize failed".into());
        }
        for (id, name, format) in [
            (DEBAND, c"deband", FORMAT_FLAG),
            (PAUSE, c"pause", FORMAT_FLAG),
            (TIME, c"time-pos", FORMAT_DOUBLE),
            (DURATION, c"duration", FORMAT_DOUBLE),
            (EOF, c"eof-reached", FORMAT_FLAG),
            (WIDTH, c"dwidth", FORMAT_INT64),
            (HEIGHT, c"dheight", FORMAT_INT64),
            (HWDEC, c"hwdec-current", FORMAT_STRING),
            (DROPS, c"frame-drop-count", FORMAT_INT64),
        ] {
            // SAFETY: valid handle; static name.
            unsafe { mpv_observe_property(s.mpv, id, name.as_ptr(), format) };
        }
        let mut init = mpv_opengl_init_params {
            get_proc_address: Some(get_proc_address),
            get_proc_address_ctx: ptr::null_mut(),
        };
        // The render node wins over the Wayland display: mpv would try the Wayland one first, and that one opens the
        // compositor's main device, which need not be this context's GPU.
        let device = if s.render_fd.is_some() {
            mpv_render_param {
                kind: RENDER_PARAM_DRM_DISPLAY_V2,
                data: (&raw mut *s.drm).cast(),
            }
        } else {
            match options.display {
                Some(d) => mpv_render_param {
                    kind: RENDER_PARAM_WL_DISPLAY,
                    data: d.as_ptr(),
                },
                None => mpv_render_param {
                    kind: RENDER_PARAM_INVALID,
                    data: ptr::null_mut(),
                },
            }
        };
        let mut params = [
            mpv_render_param {
                kind: RENDER_PARAM_API_TYPE,
                data: RENDER_API_TYPE_OPENGL.as_ptr().cast_mut().cast(),
            },
            mpv_render_param {
                kind: RENDER_PARAM_OPENGL_INIT_PARAMS,
                data: (&raw mut init).cast(),
            },
            device,
            mpv_render_param {
                kind: RENDER_PARAM_INVALID,
                data: ptr::null_mut(),
            },
        ];
        // SAFETY: params is INVALID-terminated and every pointer outlives the call (drm lives as long as the player).
        if unsafe { mpv_render_context_create(&mut s.render, s.mpv, params.as_mut_ptr()) } < 0 {
            s.render = ptr::null_mut();
            return Err("mpv OpenGL render context failed".into());
        }
        s.restore_gl();
        let pending: *const AtomicBool = &*s.update_pending;
        // SAFETY: the boxed flag outlives the render context (freed first in Drop).
        unsafe {
            mpv_render_context_set_update_callback(
                s.render,
                Some(on_update),
                pending.cast_mut().cast(),
            )
        };
        // fdclose://: mpv owns and closes the descriptor; no path ever reaches mpv.
        if !s.loadfile(fd) {
            return Err("mpv loadfile failed".into());
        }
        Ok(s)
    }

    /// Async `loadfile fdclose://N`; mpv owns `fd` on success, it is closed on failure.
    fn loadfile(&mut self, fd: OwnedFd) -> bool {
        let raw = fd.into_raw_fd();
        let url = CString::new(format!("fdclose://{raw}")).unwrap();
        if self.command(0, &[c"loadfile".as_ptr(), url.as_ptr()]) < 0 {
            // SAFETY: mpv did not take it; it is still ours.
            drop(unsafe { OwnedFd::from_raw_fd(raw) });
            return false;
        }
        true
    }

    fn command(&self, userdata: u64, args: &[*const c_char]) -> c_int {
        let mut argv: Vec<*const c_char> = args.to_vec();
        argv.push(ptr::null());
        // SAFETY: argv is NULL-terminated; mpv copies the strings before returning.
        unsafe { mpv_command_async(self.mpv, userdata, argv.as_mut_ptr()) }
    }

    fn restarting(&self) -> bool {
        self.next_fd.is_some()
    }

    fn fail(&mut self, reason: &str) {
        if !self.failed {
            self.failed = true;
            self.why = if reason.is_empty() {
                "playback failed".into()
            } else {
                ascii(reason.as_bytes(), 120)
            };
        }
    }

    fn allocate(&mut self) -> bool {
        let size = video_texture_size(
            self.video_w,
            self.video_h,
            super::math::VIDEO_MAX_TEXTURE_SIDE,
        );
        let fitted = if self.max_texture > 0 {
            video_texture_size(size[0], size[1], self.max_texture as u32)
        } else {
            size
        };
        if fitted[0] == 0 || fitted[1] == 0 {
            return false;
        }
        if self.tex.is_some() && fitted == [self.tex_w, self.tex_h] {
            return true;
        }
        let gl = self.gl.clone();
        // Immutable storage with the full mip chain: a new size is a new texture. RGBA8 stays: mpv dithers to the
        // target depth and the panel path is 8-bit.
        // SAFETY: GL calls on the current context with objects this player owns.
        unsafe {
            if let Some(tex) = self.tex.take() {
                gl.delete_texture(tex);
                (self.tex_w, self.tex_h, self.rendered) = (0, 0, false);
            }
            let Ok(tex) = gl.create_texture() else {
                self.fail("cannot create a video texture");
                return false;
            };
            self.tex = Some(tex);
            if self.fbo.is_none() {
                self.fbo = gl.create_framebuffer().ok();
            }
            gl.bind_texture(glow::TEXTURE_2D, Some(tex));
            self.anisotropy = texture_filtering(&gl);
            self.levels = video_texture_levels(fitted[0], fitted[1]);
            gl.tex_storage_2d(
                glow::TEXTURE_2D,
                self.levels as i32,
                glow::RGBA8,
                fitted[0] as i32,
                fitted[1] as i32,
            );
            gl.bind_texture(glow::TEXTURE_2D, None);
            if gl.get_error() != glow::NO_ERROR {
                // e.g. out of GPU memory: an error state, never a GL error left for the frame
                while gl.get_error() != glow::NO_ERROR {}
                self.fail(&format!(
                    "cannot allocate a {}x{} video texture",
                    fitted[0], fitted[1]
                ));
                return false;
            }
            gl.bind_framebuffer(glow::FRAMEBUFFER, self.fbo);
            gl.framebuffer_texture_2d(
                glow::FRAMEBUFFER,
                glow::COLOR_ATTACHMENT0,
                glow::TEXTURE_2D,
                Some(tex),
                0,
            );
            let complete =
                gl.check_framebuffer_status(glow::FRAMEBUFFER) == glow::FRAMEBUFFER_COMPLETE;
            gl.bind_framebuffer(glow::FRAMEBUFFER, None);
            if self.fbo.is_none() || !complete {
                self.fail("video frame buffer incomplete");
                return false;
            }
        }
        (self.tex_w, self.tex_h, self.rendered) = (fitted[0], fitted[1], false);
        self.reallocations += 1;
        true
    }

    fn event(&mut self, e: &mpv_event) {
        // While a reopen waits for the previous file to stop, that file's own events are not about the next one.
        match e.event_id {
            EVENT_IDLE => self.idle = true,
            EVENT_START_FILE => {
                self.idle = false;
                // SAFETY: mpv's event data for START_FILE.
                if let Some(start) = unsafe { e.data.cast::<mpv_event_start_file>().as_ref() }
                    && start.playlist_entry_id == self.requested
                {
                    self.started = true;
                }
            }
            EVENT_FILE_LOADED => {
                if self.started && !self.restarting() {
                    self.loaded = true;
                }
            }
            EVENT_END_FILE => {
                // SAFETY: mpv's event data for END_FILE.
                let Some(end) = (unsafe { e.data.cast::<mpv_event_end_file>().as_ref() }) else {
                    return;
                };
                if end.playlist_entry_id != self.requested {
                    return; // an older entry
                }
                if self.restarting() {
                    self.old_ended = true;
                } else if end.reason == END_FILE_REASON_ERROR {
                    let mut why = error_string(end.error).to_owned();
                    if !self.last_log.is_empty() {
                        why += ": ";
                        why += &self.last_log;
                    }
                    self.fail(&why);
                } else if !self.loaded {
                    let why = if self.last_log.is_empty() {
                        "no playable video".to_owned()
                    } else {
                        self.last_log.clone()
                    };
                    self.fail(&why);
                }
            }
            EVENT_COMMAND_REPLY => {
                if e.reply_userdata == PAUSE_COMMAND || e.reply_userdata == SEEK_COMMAND {
                    self.readback
                        .reply(e.error >= 0, Some(error_string(e.error)));
                }
            }
            EVENT_SET_PROPERTY_REPLY => {
                if e.reply_userdata == QUALITY {
                    if e.error < 0 {
                        self.quality_errors += 1;
                    } else {
                        self.quality_set += 1;
                    }
                    self.readback
                        .reply(e.error >= 0, Some(error_string(e.error)));
                }
            }
            EVENT_LOG_MESSAGE => {
                // SAFETY: mpv's event data for LOG_MESSAGE; its strings live until the next wait_event.
                let Some(m) = (unsafe { e.data.cast::<mpv_event_log_message>().as_ref() }) else {
                    return;
                };
                // SAFETY: as above.
                let (prefix, text) = unsafe { (c_str(m.prefix), c_str(m.text)) };
                if m.log_level <= LOG_LEVEL_ERROR && !interop_noise(prefix.map(|p| p.to_bytes())) {
                    self.last_log = ascii(text.map_or(&[][..], |t| t.to_bytes()), 120);
                }
                if std::env::var_os("XR_MPV_LOG").is_some() {
                    let show = |s: Option<&std::ffi::CStr>| {
                        s.map(|s| s.to_string_lossy().into_owned())
                            .unwrap_or_default()
                    };
                    eprint!("mpv[{}] {}", show(prefix), show(text));
                }
            }
            EVENT_PROPERTY_CHANGE => {
                // SAFETY: mpv's event data for PROPERTY_CHANGE.
                let Some(p) = (unsafe { e.data.cast::<mpv_event_property>().as_ref() }) else {
                    return;
                };
                if p.data.is_null() {
                    return;
                }
                let id = e.reply_userdata;
                if matches!(id, TIME | DURATION | EOF) && self.restarting() {
                    return; // per file: the previous file's
                }
                // SAFETY (each read below): `data` points to a value of `format`, as mpv documents.
                match p.format {
                    FORMAT_FLAG => {
                        let v = unsafe { *p.data.cast::<c_int>() } != 0;
                        match id {
                            PAUSE => {
                                self.paused = v;
                                self.readback.flag("pause", v);
                            }
                            DEBAND => self.readback.flag("deband", v),
                            EOF => self.eof = v,
                            _ => {}
                        }
                    }
                    FORMAT_DOUBLE => {
                        let v = unsafe { *p.data.cast::<f64>() };
                        if !v.is_finite() {
                            return;
                        }
                        if id == TIME {
                            self.position = v.max(0.0);
                            self.readback.number("time-pos", v);
                        } else if id == DURATION {
                            self.duration = v.max(0.0);
                            self.readback.number("duration", v);
                        }
                    }
                    FORMAT_INT64 => {
                        let v = unsafe { *p.data.cast::<i64>() };
                        if id == DROPS {
                            self.drops = v.max(0);
                        } else if (1..=65536).contains(&v) {
                            if id == WIDTH {
                                self.video_w = v as u32;
                            } else if id == HEIGHT {
                                self.video_h = v as u32;
                            }
                        }
                    }
                    FORMAT_STRING if id == HWDEC => {
                        let s = unsafe { c_str(*p.data.cast::<*const c_char>()) };
                        self.hwdec = ascii(s.map_or(&[][..], |s| s.to_bytes()), 32);
                    }
                    _ => {}
                }
            }
            _ => {}
        }
    }

    /// Handle what mpv has queued, at most 256 events; only the first wait may block, up to `first_timeout` s.
    fn events(&mut self, first_timeout: f64) {
        for i in 0..256 {
            // SAFETY: valid handle; the event stays valid until the next wait_event on this handle.
            let e = unsafe {
                mpv_wait_event(self.mpv, if i == 0 { first_timeout } else { 0.0 }).as_ref()
            };
            match e {
                Some(e) if e.event_id != EVENT_NONE => self.event(e),
                _ => break,
            }
        }
    }

    /// mpv leaves its own bindings behind; put back what the compositor's drawing assumes.
    fn restore_gl(&self) {
        let gl = &self.gl;
        // SAFETY: plain GL state calls on the current context.
        unsafe {
            gl.bind_framebuffer(glow::FRAMEBUFFER, None);
            gl.bind_vertex_array(None);
            for target in [
                glow::ARRAY_BUFFER,
                glow::ELEMENT_ARRAY_BUFFER,
                glow::PIXEL_UNPACK_BUFFER,
                glow::PIXEL_PACK_BUFFER,
            ] {
                gl.bind_buffer(target, None);
            }
            for i in 0..self.max_attribs {
                gl.disable_vertex_attrib_array(i as u32);
            }
            gl.pixel_store_i32(glow::UNPACK_ALIGNMENT, 4);
            gl.pixel_store_i32(glow::UNPACK_ROW_LENGTH, 0);
            gl.pixel_store_i32(glow::UNPACK_SKIP_ROWS, 0);
            gl.pixel_store_i32(glow::UNPACK_SKIP_PIXELS, 0);
            for cap in [
                glow::BLEND,
                glow::SCISSOR_TEST,
                glow::DEPTH_TEST,
                glow::STENCIL_TEST,
                glow::CULL_FACE,
            ] {
                gl.disable(cap);
            }
            gl.color_mask(true, true, true, true);
            for unit in (0..8).rev() {
                gl.active_texture(glow::TEXTURE0 + unit);
                gl.bind_texture(glow::TEXTURE_2D, None);
            }
            gl.use_program(None);
        }
    }

    /// Draw the frame mpv has ready into the texture. `show`: it becomes texture()'s picture; otherwise it is only
    /// handed back to mpv (the previous file's last frames while a reopen or close waits for mpv to stop).
    fn render_frame(&mut self, show: bool) -> bool {
        let (Some(fbo), Some(tex)) = (self.fbo, self.tex) else {
            return false;
        };
        if self.tex_w == 0 {
            return false;
        }
        let mut target = mpv_opengl_fbo {
            fbo: fbo.0.get() as c_int,
            w: self.tex_w as c_int,
            h: self.tex_h as c_int,
            internal_format: GL_RGBA8,
        };
        let (mut flip, mut block): (c_int, c_int) = (0, 0);
        let mut params = [
            mpv_render_param {
                kind: RENDER_PARAM_OPENGL_FBO,
                data: (&raw mut target).cast(),
            },
            mpv_render_param {
                kind: RENDER_PARAM_FLIP_Y,
                data: (&raw mut flip).cast(),
            },
            mpv_render_param {
                kind: RENDER_PARAM_BLOCK_FOR_TARGET_TIME,
                data: (&raw mut block).cast(),
            },
            mpv_render_param {
                kind: RENDER_PARAM_INVALID,
                data: ptr::null_mut(),
            },
        ];
        let start = Instant::now();
        // SAFETY: valid render context, INVALID-terminated params alive for the call, GL context current.
        let rc = unsafe { mpv_render_context_render(self.render, params.as_mut_ptr()) };
        let ms = start.elapsed().as_secs_f64() * 1e3;
        self.restore_gl();
        let gl = self.gl.clone();
        // Errors mpv leaves are counted, never passed on: the compositor treats a GL error as fatal for the run.
        // SAFETY: GL calls on the current context.
        for _ in 0..16 {
            if unsafe { gl.get_error() } == glow::NO_ERROR {
                break;
            }
            self.gl_errors += 1;
        }
        if rc < 0 {
            self.fail("mpv render failed");
            return false;
        }
        if !show {
            self.discarded += 1;
            return true;
        }
        // The mip chain of the new picture (one 8K frame: well under a millisecond, once per video frame).
        // SAFETY: GL calls on the current context with this player's texture.
        unsafe {
            gl.active_texture(glow::TEXTURE0);
            gl.bind_texture(glow::TEXTURE_2D, Some(tex));
            gl.generate_mipmap(glow::TEXTURE_2D);
            gl.bind_texture(glow::TEXTURE_2D, None);
            if gl.get_error() != glow::NO_ERROR {
                while gl.get_error() != glow::NO_ERROR {}
                self.gl_errors += 1;
            }
        }
        self.rendered = true;
        self.frames += 1;
        self.render_sum_ms += ms;
        self.render_max_ms = self.render_max_ms.max(ms);
        true
    }

    /// mpv_render_context_free has to destroy mpv's video output, whose thread can wait up to 200 ms for us to draw
    /// the frame it handed over. Stop playback and keep handing frames back until mpv is idle, so the free does not wait.
    fn quiesce(&mut self) {
        if self.mpv.is_null() || self.render.is_null() || self.idle {
            return;
        }
        if self.command(0, &[c"stop".as_ptr()]) < 0 {
            return;
        }
        let deadline = self.now_ns() + QUIESCE_NS;
        while !self.idle && self.now_ns() < deadline {
            self.events(0.001);
            if self.idle {
                break;
            }
            // SAFETY: valid render context, GL context current.
            if unsafe { mpv_render_context_update(self.render) } & RENDER_UPDATE_FRAME != 0 {
                self.render_frame(false);
            }
            // SAFETY: as above.
            unsafe { mpv_render_context_report_swap(self.render) };
        }
    }

    /// The previous file has stopped (or never answered): load the next one, playing as a new player would (paused if
    /// play/pause was pressed in the meantime).
    fn start_next(&mut self) {
        self.requested += 1;
        (self.started, self.old_ended) = (false, false);
        let Some(fd) = self.next_fd.take() else {
            return;
        };
        let resume = if std::mem::take(&mut self.pause_next) {
            c"yes"
        } else {
            c"no"
        };
        let resumed = self.command(0, &[c"set".as_ptr(), c"pause".as_ptr(), resume.as_ptr()]) >= 0;
        if !resumed {
            drop(fd);
            self.fail("mpv loadfile failed");
        } else if !self.loadfile(fd) {
            self.fail("mpv loadfile failed");
        }
    }

    fn now_ns(&self) -> i64 {
        // SAFETY: valid handle.
        unsafe { mpv_get_time_ns(self.mpv) }
    }

    /// Play the file behind `fd` in this player instead, keeping mpv's renderer, shaders and hardware decoder. The
    /// previous file stops over the next few update() calls; texture() is None until the new file's first frame. Err
    /// with `fd` back if this player cannot be reused (it failed, its file has not started, it has drawn about an hour
    /// of frames, or `options` differ): open a new one.
    pub fn reopen(&mut self, fd: OwnedFd, options: Options) -> Result<(), OwnedFd> {
        // Only once the current file has started: before that, mpv's own start-up events could pass for the stop's.
        if self.failed
            || !self.started
            || self.frames + self.discarded >= REUSE_MAX_FRAMES
            || options != self.options
        {
            return Err(fd);
        }
        if self.idle {
            self.old_ended = true; // nothing playing: nothing to wait for
        } else if self.command(0, &[c"stop".as_ptr()]) < 0 {
            return Err(fd); // async: update() hands back its last frames until mpv is idle
        }
        if self.next_fd.is_none() {
            self.pause_next = false;
        } // else a reopen still waiting to load: superseded (its descriptor closes below)
        self.next_fd = Some(fd);
        self.restart_since_ns = self.now_ns();
        self.reopens += 1;
        // What a new player shows: nothing until the next file's first frame.
        (self.loaded, self.eof, self.rendered) = (false, false, false);
        self.readback.restart();
        (self.position, self.duration) = (0.0, 0.0);
        self.last_log.clear();
        self.pacer.restart();
        Ok(())
    }

    pub fn toggle_pause(&mut self) {
        if self.failed {
            return;
        }
        if self.restarting() {
            self.pause_next = !self.pause_next; // applied with the next file
            return;
        }
        let rc = self.command(PAUSE_COMMAND, &[c"cycle".as_ptr(), c"pause".as_ptr()]);
        if rc < 0 {
            self.readback.reply(false, Some(error_string(rc)));
        }
    }

    pub fn seek(&mut self, relative_s: f64) {
        if self.failed || self.restarting() || !relative_s.is_finite() {
            return;
        }
        let amount = CString::new(format!("{relative_s:.6}")).unwrap();
        // exact: works between keyframes and past the last one
        let rc = self.command(
            SEEK_COMMAND,
            &[
                c"seek".as_ptr(),
                amount.as_ptr(),
                c"relative+exact".as_ptr(),
            ],
        );
        if rc < 0 {
            self.readback.reply(false, Some(error_string(rc)));
        }
    }

    /// mpv's `deband` (async; applies from the next frame mpv renders, kept across reopen).
    pub fn set_deband(&mut self, on: bool) {
        if self.failed || self.mpv.is_null() {
            return;
        }
        let mut value: *const c_char = if on { c"yes".as_ptr() } else { c"no".as_ptr() };
        // SAFETY: valid handle; data points to a C string pointer, which mpv copies.
        let rc = unsafe {
            mpv_set_property_async(
                self.mpv,
                QUALITY,
                c"deband".as_ptr(),
                FORMAT_STRING,
                (&raw mut value).cast(),
            )
        };
        if rc < 0 {
            self.quality_errors += 1;
            self.readback.reply(false, Some(error_string(rc)));
        }
    }

    /// Once per output frame, before drawing: handle mpv events and, if mpv has a frame due on this display frame,
    /// render it into texture(). Restores the GL state the compositor relies on and swallows (counts) GL errors mpv
    /// leaves. A playback failure shows as state() == Error.
    pub fn update(&mut self) {
        self.events(0.0);
        let now = self.now_ns(); // the clock of mpv's target times
        self.pacer.tick(now);
        if self.video_w != 0 && self.video_h != 0 && !self.failed {
            self.allocate();
        }
        self.update_pending.store(false, Ordering::Relaxed);
        // SAFETY: valid render context, GL context current.
        let flags = unsafe { mpv_render_context_update(self.render) };
        if self.restarting() {
            // reopen(): hand the previous file's frames back until mpv has stopped it, then load the next
            if flags & RENDER_UPDATE_FRAME != 0 {
                self.render_frame(false);
            }
            if (self.idle && self.old_ended) || now - self.restart_since_ns >= RESTART_NS {
                self.start_next();
            }
            return;
        }
        if flags & RENDER_UPDATE_FRAME == 0 || self.fbo.is_none() || self.tex_w == 0 {
            return;
        }
        // mpv hands a frame over up to 50 ms before it is due and, with BLOCK_FOR_TARGET_TIME off, leaves the timing
        // to us: hold it until its display frame; it stays pending in mpv meanwhile. target_time is in
        // mpv_get_time_ns() units (vo_libmpv.c), whatever render.h says about microseconds.
        let mut info = mpv_render_frame_info::default();
        let query = mpv_render_param {
            kind: RENDER_PARAM_NEXT_FRAME_INFO,
            data: (&raw mut info).cast(),
        };
        // SAFETY: valid render context; info outlives the call.
        let got = unsafe { mpv_render_context_get_info(self.render, query) } >= 0;
        let timed = got
            && info.flags & RENDER_FRAME_INFO_PRESENT != 0
            && info.flags & RENDER_FRAME_INFO_REDRAW == 0
            && info.target_time > 0;
        if timed && !self.pacer.due(info.target_time, now) {
            return;
        }
        if !self.render_frame(true) {
            return;
        }
        if timed {
            self.pacer.drawn(info.target_time, now);
        } else {
            self.pacer.untimed(); // a redraw: no cadence to keep
        }
    }

    /// After the buffer swap (mpv's frame timing).
    pub fn report_swap(&self) {
        if !self.render.is_null() {
            // SAFETY: valid render context.
            unsafe { mpv_render_context_report_swap(self.render) };
        }
    }

    /// None until the first frame has been rendered.
    pub fn texture(&self) -> Option<glow::Texture> {
        if self.rendered { self.tex } else { None }
    }

    /// mpv draws into the FBO top row first (FLIP_Y = 0): row 0 is the top of the picture.
    pub fn flip_y(&self) -> bool {
        false
    }

    /// Texture size: the decoded size, scaled down to VIDEO_MAX_TEXTURE_SIDE.
    pub fn width(&self) -> u32 {
        self.tex_w
    }

    pub fn height(&self) -> u32 {
        self.tex_h
    }

    pub fn state(&self) -> State {
        if self.failed {
            State::Error
        } else if !self.loaded || !self.rendered {
            State::Loading
        } else if self.eof {
            State::Ended
        } else if self.paused {
            State::Paused
        } else {
            State::Playing
        }
    }

    pub fn position_s(&self) -> f64 {
        self.position
    }

    pub fn duration_s(&self) -> f64 {
        self.duration
    }

    /// Printable ASCII, bounded; empty unless state() == Error.
    pub fn error(&self) -> &str {
        if self.failed { &self.why } else { "" }
    }

    pub fn readback(&self) -> &Readback {
        &self.readback
    }

    /// Bounded JSON: hwdec in use, sizes, frames, frame timing; no pixel data. Field order as the C++ player's.
    pub fn report(&self) -> String {
        let num = |v: f64| format!("{:.2}", if v.is_finite() { v } else { 0.0 });
        let t = self.pacer.stats();
        format!(
            concat!(
                "{{\"readback\":{},\"decoder\":\"libmpv\",\"hwdec\":\"{}\",\"va_device\":\"{}\",\"video\":[{},{}],",
                "\"texture\":[{},{}],\"frames\":{},\"mip_levels\":{},\"anisotropy\":{},\"reallocations\":{},",
                "\"gl_errors\":{},\"reopens\":{},\"discarded\":{},\"drops\":{},\"quality_set\":{},\"quality_errors\":{},",
                "\"held\":{},\"early\":{},\"late\":{},\"lead_ms\":[{},{},{}],\"render_ms\":[{},{}],\"interval_ms\":{},",
                "\"state\":\"{}\",\"error\":\"{}\"}}"
            ),
            self.readback.json(),
            self.hwdec,
            self.render_node.as_deref().unwrap_or("default"),
            self.video_w,
            self.video_h,
            self.tex_w,
            self.tex_h,
            self.frames,
            self.levels,
            num(f64::from(self.anisotropy)),
            self.reallocations,
            self.gl_errors,
            self.reopens,
            self.discarded,
            self.drops,
            self.quality_set,
            self.quality_errors,
            t.held,
            t.early,
            t.late,
            num(t.lead_min_ms),
            num(t.lead_mean_ms),
            num(t.lead_max_ms),
            num(self.render_sum_ms / self.frames.max(1) as f64),
            num(self.render_max_ms),
            num(self.pacer.interval_ns() as f64 / 1e6),
            self.state().name(),
            self.error(),
        )
    }
}

impl Drop for Player {
    fn drop(&mut self) {
        self.quiesce();
        // SAFETY: handles this player created; GL context current (the caller's contract). The render context goes
        // before the core, the render node after the VA display that uses it.
        unsafe {
            if !self.render.is_null() {
                mpv_render_context_free(self.render);
            }
            if !self.mpv.is_null() {
                mpv_terminate_destroy(self.mpv);
            }
        }
        self.next_fd = None;
        self.render_fd = None;
        // SAFETY: GL objects this player created.
        unsafe {
            if let Some(fbo) = self.fbo.take() {
                self.gl.delete_framebuffer(fbo);
            }
            if let Some(tex) = self.tex.take() {
                self.gl.delete_texture(tex);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ascii_is_json_safe() {
        assert_eq!(ascii(b"a\"b\\c\n d  ", 120), "abc d");
        assert_eq!(ascii(b"abcdef", 3), "abc");
        assert!(interop_noise(Some(b"libmpv_render/drmprime-overlay/x")));
        assert!(!interop_noise(Some(b"vd")));
    }
}
