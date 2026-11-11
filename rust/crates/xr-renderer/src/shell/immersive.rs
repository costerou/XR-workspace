//! Immersive 3D: the 3D buttons, the stereo pass and the depth engine's shared memory (wayland.cpp set_immersive,
//! set_depth_fd, depth_tick and apply_immersive; `stereo.rs`, `stereo_gl.rs`, `depth.rs`, `depth_feed.rs` and
//! `depth_link.rs` hold the ported logic).
//!
//! The depth engine is another process behind the memory `--depth-fd` names. Nothing it does reaches the frame: memory
//! of the wrong size never makes a link (the reason stays in the owner readback's `engine_error`, and is not retried),
//! and an engine that stops beating is simply no longer fed or vouched for, so its desktops ease flat by themselves.
//! Either way the 3D buttons still light and the stereo pass still runs.

use std::os::fd::{AsFd, OwnedFd};

use xr_renderer::depth_link::{DepthLink, Tick};
use xr_renderer::desktops::MAX_DESKTOPS;
use xr_renderer::gl::{GlCompositor, StereoSettings};
use xr_renderer::stereo::stereo_from_wire;
use xr_wire::control::ImmersiveParams;

use super::Presentation;
use super::scene::Scene;

/// Whether this build takes `--depth-fd`.
pub fn depth_fd_supported() -> bool {
    true
}

/// The C++ Impl's `immersive` (the 3D buttons, and whether to draw 3D) and its depth engine's end.
pub(super) struct ImmersiveState {
    desktops: [bool; MAX_DESKTOPS],
    global: bool,
    stereo: bool,
    hover: i32,
    tail: bool,
    params: Option<ImmersiveParams>,
    /// The stereo settings changed since they were last handed to the compositor.
    changed: bool,
    /// The memory, inherited for the run.
    depth_fd: Option<OwnedFd>,
    /// A link only while some desktop is 3D.
    depth: Option<DepthLink>,
    /// Why the memory could not be linked (then it is not tried again).
    depth_why: String,
    /// The desktops the channels mean: a new list restarts them (wayland.cpp set_desktops' `depth->restart()`).
    names: Vec<String>,
}

impl Default for ImmersiveState {
    fn default() -> Self {
        Self {
            desktops: [false; MAX_DESKTOPS],
            global: false,
            stereo: false,
            hover: -1,
            tail: false,
            params: None,
            changed: false,
            depth_fd: None,
            depth: None,
            depth_why: String::new(),
            names: Vec::new(),
        }
    }
}

impl ImmersiveState {
    /// The 3D buttons of `scene.glass()`, every frame (the render path's glass `immersive*` lines). The stereo settings
    /// go to the compositor in [`ImmersiveState::before_draw`], as the C++ depth_tick does, so a map of this frame
    /// already finds the 3D pass.
    pub(super) fn contribute(&mut self, scene: &mut Scene) {
        let glass = &mut scene.glass().frame;
        for (on, &mine) in glass.immersive.iter_mut().zip(&self.desktops) {
            *on = mine;
        }
        glass.immersive_global = self.global;
        glass.immersive_hover = self.hover;
        glass.immersive_tail = self.tail;
    }

    /// The C++ depth_tick: after the frame's pictures are uploaded and before the pose is read, so 3D's work never ages
    /// it. `names` are the desktops (set_desktops' list), `frames` each capture's frame count (capture order, the first
    /// MAX_DESKTOPS) and `focus` the desktop the view is on.
    pub(super) fn before_draw(&mut self, gl: &mut GlCompositor, names: &[String], frames: &[u64], focus: i32) {
        // Nothing before the first `immersive` frame: no desktop is 3D and the compositor keeps its defaults.
        let Some(params) = self.params else { return };
        if self.changed {
            // Here, with the context current: the first time on compiles the shaders.
            gl.set_stereo(&settings(self.stereo, &self.desktops, &params));
            self.changed = false;
        }
        if self.names != names {
            self.names = names.to_vec();
            if let Some(d) = &mut self.depth {
                d.restart(); // every channel now means another desktop
            }
        }
        let any = self.desktops.iter().any(|&on| on);
        let Some(fd) = self.depth_fd.as_ref().filter(|_| any) else {
            if let Some(d) = self.depth.take() {
                d.stop(); // nothing is 3D: no link, no work
            }
            return;
        };
        if self.depth.is_none() && self.depth_why.is_empty() {
            match DepthLink::open(fd.as_fd()) {
                Ok(link) => self.depth = Some(link),
                Err(why) => {
                    eprintln!("depth: {why}");
                    self.depth_why = why;
                }
            }
        }
        let Some(depth) = &mut self.depth else {
            return;
        };
        let n = frames.len().min(MAX_DESKTOPS);
        let mut wanted = [false; MAX_DESKTOPS];
        for (w, &on) in wanted.iter_mut().zip(&self.desktops).take(n) {
            *w = on;
        }
        let t = Tick {
            wanted: &wanted[..n],
            frames: &frames[..n],
            focused: focus,
            drawing: self.stereo,
            convergence: params.convergence as f32,
            range_ms: params.range_ms as f32,
            now_ns: monotonic_ns(),
        };
        depth.tick(gl, &t);
    }

    /// No immersive part animates on its own (the compositor's 3D fade rides on the frames the captures bring).
    pub(super) fn animating(&self, _now_ms: f64) -> bool {
        false
    }

    /// The owner readback's `immersive`: the compositor's `stereo_readback` with each desktop's monitor, the engine's
    /// report (`null` without a link) and why there is none (wayland.cpp owner readback). Keys sorted, as nlohmann's.
    pub(super) fn readback(&self, stereo: &str, names: &[&str]) -> String {
        let Ok(serde_json::Value::Object(mut im)) = serde_json::from_str::<serde_json::Value>(stereo) else {
            return stereo.to_owned();
        };
        if let Some(serde_json::Value::Array(desktops)) = im.get_mut("desktops") {
            for (d, name) in desktops.iter_mut().zip(names) {
                if let serde_json::Value::Object(d) = d {
                    d.insert("monitor".into(), (*name).into());
                }
            }
        }
        let engine = self
            .depth
            .as_ref()
            .and_then(|d| serde_json::from_str(&d.report()).ok())
            .unwrap_or(serde_json::Value::Null);
        im.insert("engine".into(), engine);
        im.insert("engine_error".into(), self.depth_why.chars().take(256).collect::<String>().into());
        serde_json::Value::Object(im).to_string()
    }

    /// The exit report's `depth` section (after `composition`), only while a link exists.
    pub(super) fn report(&self, out: &mut String) {
        if let Some(d) = &self.depth {
            out.push_str(",\"depth\":");
            out.push_str(&d.report());
        }
    }

    /// Before the compositor goes (wayland.cpp ~Impl: "its GL objects first").
    pub(super) fn close(&mut self) {
        self.depth = None;
    }
}

/// The C++ apply_immersive: 3D on or off, its look, per-desktop strength (-1: the global one) and which desktops take
/// part.
fn settings(stereo: bool, desktops: &[bool; MAX_DESKTOPS], params: &ImmersiveParams) -> StereoSettings {
    StereoSettings {
        enabled: stereo,
        params: stereo_from_wire(params),
        strengths: params.strengths.unwrap_or([-1; MAX_DESKTOPS]).to_vec(),
        desktops: desktops.to_vec(),
        ..StereoSettings::default()
    }
}

fn monotonic_ns() -> u64 {
    let ts = rustix::time::clock_gettime(rustix::time::ClockId::Monotonic);
    ts.tv_sec as u64 * 1_000_000_000 + ts.tv_nsec as u64
}

impl Presentation {
    pub fn set_immersive(
        &mut self,
        desktops: &[bool; 4],
        global: bool,
        stereo: bool,
        hover: i32,
        tail: bool,
        params: &ImmersiveParams,
    ) {
        let i = &mut self.state.features.immersive;
        // A hover alone costs the compositor nothing.
        i.changed = i.changed || i.stereo != stereo || i.desktops != *desktops || i.params != Some(*params);
        i.desktops = *desktops;
        i.global = global;
        i.stereo = stereo;
        i.hover = hover;
        i.tail = tail;
        i.params = Some(*params);
        self.state.capture.invalidate();
    }

    /// The depth engine's shared memory (a memfd the supervisor made); it stays open for the run.
    pub fn set_depth_fd(&mut self, fd: OwnedFd) {
        self.state.features.immersive.depth_fd = Some(fd);
    }
}

#[cfg(test)]
mod tests {
    //! Review focus 4 at the module: memory that is not a valid XRD1 block never makes a link (and is not tried again),
    //! yet the 3D buttons light and the stereo settings still reach the compositor; no 3D desktop means no link. An
    //! engine dying mid-run is `capture_e2e_rust_immersive_engine_dies` and tests/depth_link_render.rs.

    use std::os::fd::OwnedFd;

    use rustix::fs::{MemfdFlags, ftruncate, memfd_create};
    use xr_renderer::depth::{DEPTH_LINK_SHM, depth_shm_bytes};
    use xr_renderer::gl::GlCompositor;
    use xr_renderer::view::FRect;
    use xr_wire::control::ImmersiveParams;

    use super::super::scene::Scene;
    use super::ImmersiveState;

    const PARAMS: ImmersiveParams = ImmersiveParams {
        strength: 100,
        convergence: 50,
        near_arcmin: 60,
        far_arcmin: 60,
        text: 100,
        range_ms: 0,
        strengths: None,
    };

    fn memfd(bytes: usize) -> OwnedFd {
        let fd = memfd_create("xr-depth-test", MemfdFlags::CLOEXEC).unwrap();
        ftruncate(&fd, bytes as u64).unwrap();
        fd
    }

    fn gl() -> Option<GlCompositor> {
        match GlCompositor::create_offscreen(320, 120, 1) {
            Ok(mut gl) => {
                gl.resize(320, 120, 320, 120);
                let grey = vec![0x40u8; 64 * 36 * 4];
                assert!(gl.upload(0, &grey, 64, 36, 64 * 4));
                Some(gl)
            }
            Err(why) => {
                println!("SKIP: no offscreen compositor: {why}");
                None
            }
        }
    }

    fn on(fd: Option<OwnedFd>) -> ImmersiveState {
        ImmersiveState {
            desktops: [true, false, false, false],
            global: true,
            stereo: true,
            hover: 0,
            tail: true,
            params: Some(PARAMS),
            changed: true,
            depth_fd: fd,
            ..ImmersiveState::default()
        }
    }

    fn readback(i: &ImmersiveState, gl: &GlCompositor) -> serde_json::Value {
        serde_json::from_str(&i.readback(&gl.stereo_readback(), &["XR-1"])).unwrap()
    }

    #[test]
    fn memory_that_is_not_xrd1_makes_no_link_and_the_buttons_still_light() {
        let Some(mut gl) = gl() else { return };
        let names = vec![String::from("XR-1")];
        for bytes in [0, 4096, depth_shm_bytes(&DEPTH_LINK_SHM) + 1] {
            let mut i = on(Some(memfd(bytes)));
            for frame in 1..=3 {
                i.before_draw(&mut gl, &names, &[frame], 0);
            }
            assert!(i.depth.is_none() && !i.depth_why.is_empty(), "{bytes} bytes: {}", i.depth_why);
            let why = i.depth_why.clone();
            i.before_draw(&mut gl, &names, &[4], 0);
            assert_eq!(i.depth_why, why, "not tried again");
            let mut report = String::new();
            i.report(&mut report);
            assert!(report.is_empty(), "no depth section without a link");
            let r = readback(&i, &gl);
            assert!(r["engine"].is_null() && r["engine_error"] == why.as_str());
            assert!(r["available"].is_boolean() && r["enabled"] == true, "the stereo settings reached the compositor");
            assert_eq!(r["desktops"][0]["monitor"], "XR-1");
            assert_eq!(r["desktops"][0]["depth_ready"], false, "no desktop gets depth");

            let mut scene = Scene::new(0.0, (320, 120), (1.0, 0.5), vec![FRect::new(10.0, 10.0, 300.0, 100.0)]);
            i.contribute(&mut scene);
            let g = &scene.glass.as_ref().unwrap().frame;
            assert!(g.immersive[0] && !g.immersive[1] && g.immersive_global && g.immersive_tail);
            assert_eq!(g.immersive_hover, 0);
        }
    }

    #[test]
    fn a_link_only_while_some_desktop_is_3d() {
        let Some(mut gl) = gl() else { return };
        let names = vec![String::from("XR-1")];
        let mut i = on(Some(memfd(depth_shm_bytes(&DEPTH_LINK_SHM))));
        i.before_draw(&mut gl, &names, &[1], 0);
        assert!(i.depth.is_some() && i.depth_why.is_empty());
        let r = readback(&i, &gl);
        assert_eq!(r["engine"]["alive"], false, "no heartbeat: not alive");
        let mut report = String::new();
        i.report(&mut report);
        assert!(report.starts_with(",\"depth\":{\"alive\":false,"), "{report}");
        i.desktops = [false; 4];
        i.changed = true;
        i.before_draw(&mut gl, &names, &[2], 0);
        assert!(i.depth.is_none(), "nothing is 3D: no link");
        i.close();
    }

    #[test]
    fn no_memory_no_link() {
        let Some(mut gl) = gl() else { return };
        let mut i = on(None);
        i.before_draw(&mut gl, &[String::from("XR-1")], &[1], 0);
        assert!(i.depth.is_none() && i.depth_why.is_empty());
        let r = readback(&i, &gl);
        assert!(r["engine"].is_null() && r["engine_error"] == "");
    }
}
