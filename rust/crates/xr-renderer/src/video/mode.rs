//! VR video mode of the renderer (docs/vr-video.md; port of spike/src/video_mode.cpp without the controls card, which
//! comes with the HUD): while active it replaces the desktops in the glasses with the head-tracked video. Owns the
//! decoder and the projection pass; the supervisor owns what is shown and sends it as `video` control frames. The GL
//! context is current for every call and for drop.

use std::os::fd::OwnedFd;
use std::rc::Rc;

use glow::HasContext;
use serde_json::{Value, json};

use super::math::{
    Filter, Format, Mat3, Projection, Quality, State, Stereo, eye_viewports, half_fov_tan,
};
use super::player::{Options, Player};
use super::sphere::Sphere;

/// What goes behind the video, drawn into the current viewport seen through tan(half FOV) half_w, half_h; false if
/// nothing was drawn.
pub type Behind<'a> = &'a dyn Fn(f32, f32) -> bool;

pub struct Mode {
    gl: Rc<glow::Context>,
    active: bool,
    format: Format,
    why: String,
    // Field order is drop order: the player (mpv's render context) goes first.
    player: Option<Player>,
    sphere: Option<Sphere>,
    quality: Quality,
    format_drawn: Option<Format>,
    filter_drawn: Option<Filter>,
    opened: u64,
    frames_drawn: u64,
}

impl Mode {
    pub fn new(gl: Rc<glow::Context>) -> Self {
        Self {
            gl,
            active: false,
            format: Format::default(),
            why: String::new(),
            player: None,
            sphere: None,
            quality: Quality::default(),
            format_drawn: None,
            filter_drawn: None,
            opened: 0,
            frames_drawn: 0,
        }
    }

    /// Start playing the file behind `fd`. A failure keeps the mode active in the error state; the supervisor then
    /// closes it.
    pub fn open(&mut self, fd: OwnedFd, format: Format, audio: bool) {
        (self.format_drawn, self.filter_drawn) = (None, None);
        (self.active, self.format) = (true, format);
        self.why.clear();
        self.opened += 1;
        if self.sphere.is_none() {
            match Sphere::new(self.gl.clone()) {
                Ok(s) => self.sphere = Some(s),
                Err(e) => self.why = e,
            }
        }
        let options = Options {
            audio,
            hwdec: true,
            display: None,
        };
        if self.sphere.is_none() {
            drop(fd);
            self.player = None;
        } else {
            // A web video's seek (a relay restart) or another video while one plays: the same player, without tearing
            // mpv down.
            let fd = match self.player.as_mut() {
                Some(p) => p.reopen(fd, options).err(),
                None => Some(fd),
            };
            if let Some(fd) = fd {
                self.player = None;
                match Player::open(self.gl.clone(), fd, options) {
                    Ok(p) => self.player = Some(p),
                    Err(e) => self.why = e,
                }
            }
        }
        self.apply_quality();
        self.prepare();
    }

    pub fn close(&mut self) {
        self.player = None; // mpv's render context first, while the GL context is current
        self.active = false;
        (self.format_drawn, self.filter_drawn) = (None, None);
    }

    pub fn active(&self) -> bool {
        self.active
    }

    pub fn toggle_pause(&mut self) {
        if let Some(p) = &mut self.player {
            p.toggle_pause();
        }
    }

    pub fn seek(&mut self, relative_s: f64) {
        if let Some(p) = &mut self.player {
            p.seek(relative_s);
        }
    }

    pub fn set_format(&mut self, format: Format) {
        self.format = format;
    }

    /// Applied at once to the playing video and kept for later ones.
    pub fn set_quality(&mut self, quality: Quality) {
        self.quality = quality;
        self.apply_quality();
    }

    pub fn quality(&self) -> Quality {
        self.quality
    }

    /// The player's settings now; the texture filter is set in draw(), on whatever texture holds the frame.
    fn apply_quality(&mut self) {
        let deband = self.quality.deband;
        if let Some(p) = &mut self.player {
            p.set_deband(deband);
        }
    }

    pub fn state(&self) -> State {
        match (&self.player, self.active) {
            (_, false) => State::Off,
            (None, true) => State::Error,
            (Some(p), true) => p.state(),
        }
    }

    pub fn player(&self) -> Option<&Player> {
        self.player.as_ref()
    }

    /// Before drawing a frame: decode and update.
    pub fn prepare(&mut self) {
        if !self.active {
            return;
        }
        if let Some(p) = &mut self.player {
            p.update();
        }
    }

    /// Draw the video into the current framebuffer (`width` x `height`), one viewport per output eye, over
    /// `background` (pixels outside the picture show it; black without one). A 360° picture covers everything, so
    /// the background is not drawn for one (while nothing is showing yet, it is).
    pub fn draw(
        &mut self,
        view: &Mat3,
        diagonal_fov_deg: f32,
        width: u32,
        height: u32,
        background: Option<Behind>,
    ) {
        let gl = self.gl.clone();
        // SAFETY (all GL blocks): plain GL calls on the current context.
        unsafe {
            gl.clear_color(0.0, 0.0, 0.0, 1.0);
            gl.viewport(0, 0, width as i32, height as i32);
            gl.clear(glow::COLOR_BUFFER_BIT);
        }
        let tex = self.player.as_ref().and_then(Player::texture);
        let picture = match (self.active, &mut self.sphere, tex) {
            (true, Some(sphere), Some(tex)) => {
                // Every frame: the player may replace its texture (a new size, a new player). Minified areas keep the
                // player's trilinear mipmaps; only magnification changes.
                if !sphere.forced() {
                    sphere.set_filter(self.quality.filter);
                }
                let mag = if sphere.filter() == Filter::Nearest {
                    glow::NEAREST
                } else {
                    glow::LINEAR
                };
                unsafe {
                    gl.active_texture(glow::TEXTURE0);
                    gl.bind_texture(glow::TEXTURE_2D, Some(tex));
                    gl.tex_parameter_i32(
                        glow::TEXTURE_2D,
                        glow::TEXTURE_MIN_FILTER,
                        glow::LINEAR_MIPMAP_LINEAR as i32,
                    );
                    gl.tex_parameter_i32(glow::TEXTURE_2D, glow::TEXTURE_MAG_FILTER, mag as i32);
                }
                Some((&*sphere, tex))
            }
            _ => None,
        };
        // A 360° picture leaves nothing of the background to show.
        let covered = picture.is_some() && self.format.projection == Projection::Equirect360;
        let flip_y = self.player.as_ref().is_some_and(Player::flip_y);
        for e in eye_viewports(width, height) {
            unsafe { gl.viewport(e.x as i32, e.y as i32, e.width as i32, e.height as i32) };
            let [hw, hh] = half_fov_tan(diagonal_fov_deg, e.width, e.height);
            let behind = !covered && background.is_some_and(|b| b(hw, hh));
            if let Some((sphere, tex)) = picture {
                sphere.draw(tex, flip_y, &self.format, e.eye, view, hw, hh, behind);
            }
        }
        unsafe { gl.viewport(0, 0, width as i32, height as i32) };
        if let Some((sphere, _)) = picture {
            let filter = sphere.filter();
            self.frames_drawn += 1;
            (self.format_drawn, self.filter_drawn) = (Some(self.format), Some(filter));
        }
    }

    pub fn after_swap(&self) {
        if let Some(p) = &self.player {
            p.report_swap();
        }
    }

    /// Bounded JSON for the exit report, as the C++ mode writes it (the card fields read 0 until the HUD lands).
    pub fn report(&self) -> String {
        let format = |f: &Format| {
            let projection = match f.projection {
                Projection::Equirect360 => "360",
                Projection::Equirect180 => "180",
                Projection::Fisheye => "fisheye",
            };
            let stereo = match f.stereo {
                Stereo::Mono => "mono",
                Stereo::Sbs => "sbs",
                Stereo::Tb => "tb",
            };
            json!({"projection": projection, "stereo": stereo, "fov": f.fov_deg})
        };
        let player = self.player.as_ref().map(Player::report);
        let parsed: Value = player
            .as_deref()
            .and_then(|p| serde_json::from_str(p).ok())
            .unwrap_or(Value::Null);
        let observed = parsed.get("readback").cloned().unwrap_or_else(|| json!({}));
        let get = |k: &str, d: Value| observed.get(k).cloned().unwrap_or(d);
        let error: String = self
            .player
            .as_ref()
            .map_or(self.why.as_str(), Player::error)
            .chars()
            .take(256)
            .collect();
        let typed = json!({
            "format_state": format(&self.format),
            "format_applied": self.format_drawn.as_ref().map(format),
            "playback": {
                "state": self.state().name(),
                "paused": get("paused", Value::Null),
                "position_s": get("position_s", Value::Null),
                "duration_s": get("duration_s", Value::Null),
                "revision": get("revision", json!(0)),
                "command_replies": get("command_replies", json!(0)),
                "command_errors": get("command_errors", json!(0)),
                "command_error": get("command_error", json!("")),
                "error": error,
            },
            "quality_readback": {
                "filter_requested": self.quality.filter.name(),
                "deband_requested": self.quality.deband,
                "filter_applied": self.filter_drawn.map(Filter::name),
                "deband_applied": get("deband_applied", Value::Null),
            },
        });
        let prefix = typed.to_string();
        let filter = self
            .sphere
            .as_ref()
            .map_or(self.quality.filter, Sphere::filter);
        format!(
            "{},\"opened\":{},\"frames_drawn\":{},\"card_shown\":0,\"state\":\"{}\",\"format\":\"{}\",\"filter\":\"{}\",\
             \"forced\":{},\"setting\":\"{}\",\"deband\":{},\"player\":{}}}",
            &prefix[..prefix.len() - 1],
            self.opened,
            self.frames_drawn,
            self.state().name(),
            self.format.label(true),
            filter.name(),
            self.sphere.as_ref().is_some_and(Sphere::forced),
            self.quality.filter.name(),
            self.quality.deband,
            player.as_deref().unwrap_or("null"),
        )
    }
}

impl Drop for Mode {
    fn drop(&mut self) {
        self.close();
    }
}
