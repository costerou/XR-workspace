//! VR video on a surfaceless EGL context (no display, window or compositor); port of spike/tests/video_render_test.cpp:
//!  1. the projection pass (Sphere) against the CPU maths (video_uv) for every projection/stereo layout, both eyes and a
//!     turned head, on a gradient texture; 1b. sampling (filters, mipmaps, the 360° seam, no bleed between eyes);
//!  3. with ffmpeg: clips generated here (never downloaded) in several codecs and layouts are decoded by the real
//!     Player, checked for orientation, pause, seek and end, and projected through Sphere; a web video as the run
//!     relays it (ffmpeg copying a clip into NUT on a pipe); a file that is not a video;
//!  5. reopen() plays the next file or stream in the same player with nothing of the previous one shown, and closing a
//!     playing video does not wait for mpv's 200 ms frame timeout;
//!  6. quality settings apply live through Mode and carry over to the next video;
//!  7. the background behind a video: once per output eye, shown where a 180° picture does not reach, never for 360°.
//!
//! The C++ test's section 2 (the controls card) comes with the HUD. Exit 77 (skipped) without EGL/GLES.
//! argv: [ffmpeg path] [scratch dir].

use std::fs::File;
use std::os::fd::{AsRawFd, OwnedFd};
use std::path::Path;
use std::process::{Child, Command, Stdio};
use std::rc::Rc;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::{Duration, Instant};

use glow::{HasContext, PixelPackData};
use serde_json::Value;
use xr_renderer::egl::Headless;
use xr_renderer::video::math::*;
use xr_renderer::video::mode::Mode;
use xr_renderer::video::player::{Options, Player};
use xr_renderer::video::sphere::{Sphere, texture_filtering};

static FAILURES: AtomicUsize = AtomicUsize::new(0);

macro_rules! check {
    ($c:expr) => {{
        let ok: bool = $c;
        if !ok {
            eprintln!("{}:{}: check!({})", file!(), line!(), stringify!($c));
            FAILURES.fetch_add(1, Ordering::Relaxed);
        }
    }};
}

type Gl = Rc<glow::Context>;

struct Target {
    gl: Gl,
    fb: glow::Framebuffer,
    rb: glow::Renderbuffer,
}

impl Target {
    fn new(gl: &Gl, w: i32, h: i32) -> Self {
        // SAFETY (all GL in this file): plain GL calls on the headless context, current on this thread.
        unsafe {
            let fb = gl.create_framebuffer().unwrap();
            gl.bind_framebuffer(glow::FRAMEBUFFER, Some(fb));
            let rb = gl.create_renderbuffer().unwrap();
            gl.bind_renderbuffer(glow::RENDERBUFFER, Some(rb));
            gl.renderbuffer_storage(glow::RENDERBUFFER, glow::RGBA8, w, h);
            gl.framebuffer_renderbuffer(
                glow::FRAMEBUFFER,
                glow::COLOR_ATTACHMENT0,
                glow::RENDERBUFFER,
                Some(rb),
            );
            Self {
                gl: gl.clone(),
                fb,
                rb,
            }
        }
    }

    fn bind(&self) {
        unsafe { self.gl.bind_framebuffer(glow::FRAMEBUFFER, Some(self.fb)) };
    }
}

impl Drop for Target {
    fn drop(&mut self) {
        unsafe {
            self.gl.bind_framebuffer(glow::FRAMEBUFFER, None);
            self.gl.delete_framebuffer(self.fb);
            self.gl.delete_renderbuffer(self.rb);
        }
    }
}

fn read(gl: &Gl, x: i32, y: i32, w: i32, h: i32) -> Vec<u8> {
    let mut px = vec![0u8; (w * h * 4) as usize];
    unsafe {
        gl.read_pixels(
            x,
            y,
            w,
            h,
            glow::RGBA,
            glow::UNSIGNED_BYTE,
            PixelPackData::Slice(Some(&mut px)),
        );
    }
    px
}

fn at(px: &[u8], w: i32, x: i32, y: i32) -> &[u8] {
    let i = ((y * w + x) * 4) as usize;
    &px[i..i + 4]
}

fn plain_texture(gl: &Gl, w: i32, h: i32, rgba: &[u8]) -> glow::Texture {
    unsafe {
        let id = gl.create_texture().unwrap();
        gl.bind_texture(glow::TEXTURE_2D, Some(id));
        gl.tex_parameter_i32(
            glow::TEXTURE_2D,
            glow::TEXTURE_MIN_FILTER,
            glow::LINEAR as i32,
        );
        gl.tex_parameter_i32(
            glow::TEXTURE_2D,
            glow::TEXTURE_MAG_FILTER,
            glow::LINEAR as i32,
        );
        gl.tex_parameter_i32(
            glow::TEXTURE_2D,
            glow::TEXTURE_WRAP_S,
            glow::CLAMP_TO_EDGE as i32,
        );
        gl.tex_parameter_i32(
            glow::TEXTURE_2D,
            glow::TEXTURE_WRAP_T,
            glow::CLAMP_TO_EDGE as i32,
        );
        gl.tex_image_2d(
            glow::TEXTURE_2D,
            0,
            glow::RGBA as i32,
            w,
            h,
            0,
            glow::RGBA,
            glow::UNSIGNED_BYTE,
            glow::PixelUnpackData::Slice(Some(rgba)),
        );
        id
    }
}

/// A video texture as Player sets it up (immutable, full mip chain, trilinear, anisotropic) from RGBA rows.
fn video_texture(gl: &Gl, w: i32, h: i32, rgba: &[u8]) -> glow::Texture {
    unsafe {
        let id = gl.create_texture().unwrap();
        gl.bind_texture(glow::TEXTURE_2D, Some(id));
        texture_filtering(gl);
        gl.tex_storage_2d(
            glow::TEXTURE_2D,
            video_texture_levels(w as u32, h as u32) as i32,
            glow::RGBA8,
            w,
            h,
        );
        gl.tex_sub_image_2d(
            glow::TEXTURE_2D,
            0,
            0,
            0,
            w,
            h,
            glow::RGBA,
            glow::UNSIGNED_BYTE,
            glow::PixelUnpackData::Slice(Some(rgba)),
        );
        gl.generate_mipmap(glow::TEXTURE_2D);
        id
    }
}

fn picture(w: i32, h: i32, rgb: impl Fn(i32, i32) -> [u8; 3]) -> Vec<u8> {
    let mut px = Vec::with_capacity((w * h * 4) as usize);
    for y in 0..h {
        for x in 0..w {
            let c = rgb(x, y);
            px.extend_from_slice(&[c[0], c[1], c[2], 255]);
        }
    }
    px
}

const AHEAD: Mat3 = IDENTITY;
const BEHIND: Mat3 = [-1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, -1.0]; // behind: the 360° seam at the centre

fn turn_y(a: f32) -> Mat3 {
    let (s, c) = a.sin_cos();
    [c, 0.0, s, 0.0, 1.0, 0.0, -s, 0.0, c]
}

fn clear(gl: &Gl, w: i32, h: i32) {
    unsafe {
        gl.viewport(0, 0, w, h);
        gl.clear_color(1.0, 0.0, 1.0, 1.0);
        gl.clear(glow::COLOR_BUFFER_BIT);
    }
}

// 1. Gradient texture: R = u, G = v (v = 0 at the top: texture row 0 is uploaded first = t 0).
fn projection_matches_cpu(gl: &Gl) {
    const T: i32 = 256;
    let tex = picture(T, T, |x, y| [x as u8, y as u8, 200]);
    // Linear, as a video texture is sampled: the cubic reproduces a linear gradient exactly, so the CPU maths hold.
    let id = plain_texture(gl, T, T, &tex);
    let sphere = match Sphere::new(gl.clone()) {
        Ok(s) => s,
        Err(e) => {
            check!(false);
            eprintln!("  {e}");
            return;
        }
    };
    const W: i32 = 240;
    const H: i32 = 150;
    let _t = Target::new(gl, W, H);
    let (c, s) = (0.6f32.cos(), 0.6f32.sin());
    let views: [Mat3; 3] = [
        IDENTITY,
        [c, 0.0, s, 0.0, 1.0, 0.0, -s, 0.0, c],
        [1.0, 0.0, 0.0, 0.0, c, -s, 0.0, s, c],
    ];
    use Projection::*;
    use Stereo::*;
    let formats = [
        Format::default(),
        Format::new(Equirect360, Tb, 180),
        Format::new(Equirect360, Sbs, 180),
        Format::new(Equirect180, Sbs, 180),
        Format::new(Equirect180, Mono, 180),
        Format::new(Fisheye, Sbs, 190),
        Format::new(Fisheye, Tb, 220),
    ];
    let (hw, hh) = (3.0f32, 2.0f32); // ±72° x ±63°: with the turned heads, 180° edges and fisheye circles fall inside
    let (mut compared, mut outside) = (0, 0);
    for f in &formats {
        for eye in 0..2 {
            for view in &views {
                clear(gl, W, H);
                sphere.draw(id, false, f, eye, view, hw, hh, false);
                let px = read(gl, 0, 0, W, H);
                for y in (3..H).step_by(9) {
                    for x in (3..W).step_by(9) {
                        let nx = (x as f32 + 0.5) / W as f32 * 2.0 - 1.0;
                        let ny = (y as f32 + 0.5) / H as f32 * 2.0 - 1.0; // read row 0 = bottom = ndc -1
                        let p = at(&px, W, x, y);
                        let Some(uv) = video_uv(f, view_ray(view, nx, ny, hw, hh), eye) else {
                            // Outside the picture: black (allow a sliver at the boundary).
                            let near = [(0.03, 0.0), (-0.03, 0.0), (0.0, 0.03), (0.0, -0.03)]
                                .iter()
                                .any(|(dx, dy)| {
                                    video_uv(f, view_ray(view, nx + dx, ny + dy, hw, hh), eye)
                                        .is_some()
                                });
                            if !near {
                                check!(p[0] == 0 && p[1] == 0 && p[2] == 0);
                                outside += 1;
                            }
                            continue;
                        };
                        let r = eye_rect(f.stereo, eye);
                        let (lu, lv) = ((uv[0] - r.x) / r.w, (uv[1] - r.y) / r.h);
                        if !(0.03..=0.97).contains(&lu) || !(0.03..=0.97).contains(&lv) {
                            continue; // texel boundaries, the 360° seam
                        }
                        let (eu, ev) = (uv[0] * T as f32 - 0.5, uv[1] * T as f32 - 0.5);
                        let ok = (p[0] as f32 - eu).abs() <= 1.6
                            && (p[1] as f32 - ev).abs() <= 1.6
                            && p[2] == 200;
                        if !ok {
                            eprintln!(
                                "  format {:?}/{:?} eye {eye} at ({x},{y}): got {} {}, expected {eu:.1} {ev:.1}",
                                f.projection, f.stereo, p[0], p[1]
                            );
                        }
                        check!(ok);
                        compared += 1;
                    }
                }
            }
        }
    }
    check!(compared > 2000 && outside > 100);
    println!(
        "projection: {compared} samples match the CPU maths, {outside} outside-picture samples black"
    );
    unsafe { gl.delete_texture(id) };
}

// 1b. Sampling (docs/vr-video.md#sampling): sharper than bilinear where magnified, no aliasing where minified, no mip
//     line along the 360° seam, nothing of the other eye at an eye's edge.
fn sampling(gl: &Gl) {
    check!(
        Filter::from_name(None) == Filter::CatmullRom
            && Filter::from_name(Some("bogus")) == Filter::CatmullRom
    );
    check!(
        Filter::from_name(Some("bilinear")) == Filter::Bilinear
            && Filter::from_name(Some("sharp")) == Filter::Sharp
    );
    check!(Filter::Bilinear.cubic_a() == 0.0 && Filter::CatmullRom.cubic_a() == -0.5);
    check!(
        video_texture_levels(8192, 4096) == 14
            && video_texture_levels(7680, 3840) == 13
            && video_texture_levels(1, 1) == 1
    );
    let mut sphere = match Sphere::new(gl.clone()) {
        Ok(s) => s,
        Err(e) => {
            check!(false);
            eprintln!("  {e}");
            return;
        }
    };
    check!(sphere.filter() == Filter::CatmullRom);
    const W: i32 = 240;
    const H: i32 = 150;
    let _t = Target::new(gl, W, H);
    let mono = Format::default();
    let draw = |sphere: &Sphere, tex, f: &Format, eye, view: &Mat3, hw, hh| {
        clear(gl, W, H);
        sphere.draw(tex, false, f, eye, view, hw, hh, false);
        read(gl, 0, 0, W, H)
    };
    // Magnified ~40x: a black|white step at the centre of the view. The steepest step between neighbouring pixels is
    // the edge's sharpness: 1/40 of the range per pixel for bilinear, about 1.25x that for Catmull-Rom.
    {
        let step = video_texture(
            gl,
            64,
            32,
            &picture(64, 32, |x, _| if x < 32 { [0; 3] } else { [255; 3] }),
        );
        let mut steepest = |f| {
            sphere.set_filter(f);
            let px = draw(&sphere, step, &mono, 0, &AHEAD, 0.3, 0.19);
            let row: Vec<i32> = (0..W).map(|x| at(&px, W, x, H / 2)[0] as i32).collect();
            let most = row
                .windows(2)
                .map(|w| (w[1] - w[0]).abs())
                .max()
                .unwrap_or(0);
            check!(row.iter().min() == Some(&0) && row.iter().max() == Some(&255));
            most
        };
        let (bilinear, catmull, sharp) = (
            steepest(Filter::Bilinear),
            steepest(Filter::CatmullRom),
            steepest(Filter::Sharp),
        );
        sphere.set_filter(Filter::CatmullRom);
        println!(
            "sampling: magnified edge, steepest step bilinear {bilinear}, catmull-rom {catmull}, sharp {sharp} (of 255)"
        );
        check!(bilinear >= 4 && catmull * 100 >= bilinear * 115 && sharp >= bilinear);
        unsafe { gl.delete_texture(step) };
    }
    // Minified (~8 texels per pixel at the centre, more towards the poles): a one-texel checker should average to grey.
    // Without mipmaps it aliases into black/white noise; near the poles 16x anisotropic filtering still beats a little,
    // so the check is on the share of pixels near grey.
    {
        let pixels = picture(
            2048,
            1024,
            |x, y| if (x + y) % 2 == 1 { [255; 3] } else { [0; 3] },
        );
        let checker = video_texture(gl, 2048, 1024, &pixels);
        let plain = plain_texture(gl, 2048, 1024, &pixels); // the old texture: linear, no mipmaps
        let grey = |tex| {
            let px = draw(&sphere, tex, &mono, 0, &AHEAD, 3.0, 2.0);
            let near = px
                .chunks(4)
                .filter(|p| (p[0] as i32 - 128).abs() <= 24)
                .count();
            100.0 * near as f64 / (W * H) as f64
        };
        let (now, before) = (grey(checker), grey(plain));
        println!(
            "sampling: minified checker, {now:.1}% of pixels within 24 of grey ({before:.1}% without mipmaps)"
        );
        check!(now >= 98.0 && before + 20.0 < now);
        unsafe {
            gl.delete_texture(checker);
            gl.delete_texture(plain);
        }
    }
    // The 360° seam, looking straight at it, minified: outer quarters green, middle red. A seam picking the smallest
    // mip would show a column of the whole frame's average.
    {
        let seam = video_texture(
            gl,
            1024,
            512,
            &picture(1024, 512, |x, _| {
                if !(256..768).contains(&x) {
                    [0, 255, 0]
                } else {
                    [255, 0, 0]
                }
            }),
        );
        let mut bad = 0;
        for k in 0..4 {
            // the seam between, then inside, the GPU's 2x2 derivative quads (a pixel is ~0.025 rad)
            let px = draw(
                &sphere,
                seam,
                &mono,
                0,
                &turn_y(std::f32::consts::PI + 0.006 * k as f32),
                3.0,
                2.0,
            );
            bad += px.chunks(4).filter(|p| !(p[1] > 230 && p[0] < 25)).count();
        }
        println!(
            "sampling: 360 seam, {bad} of {} pixels not green",
            4 * W * H
        );
        check!(bad == 0);
        unsafe { gl.delete_texture(seam) };
    }
    // Side-by-side 360°: each eye's edge (behind the viewer) borders the other eye's picture; magnified, so the cubic's
    // taps reach over it unless clamped inside the eye.
    {
        let sbs = video_texture(
            gl,
            128,
            64,
            &picture(
                128,
                64,
                |x, _| if x < 64 { [255, 0, 0] } else { [0, 255, 0] },
            ),
        );
        let f = Format::new(Projection::Equirect360, Stereo::Sbs, 180);
        for eye in 0..2 {
            let px = draw(&sphere, sbs, &f, eye, &BEHIND, 0.3, 0.19);
            let bleed = px
                .chunks(4)
                .filter(|p| p[if eye == 0 { 1 } else { 0 }] != 0)
                .count();
            if bleed != 0 {
                eprintln!("  eye {eye}: {bleed} pixels of the other eye");
            }
            check!(bleed == 0);
        }
        unsafe { gl.delete_texture(sbs) };
    }
    unsafe { check!(gl.get_error() == glow::NO_ERROR) };
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum Colour {
    Yellow,
    Green,
    Red,
    Blue,
    Other,
}

fn classify(p: &[u8]) -> Colour {
    let (r, g, b) = (p[0], p[1], p[2]);
    if r > 170 && g > 170 && b < 90 {
        Colour::Yellow
    } else if r < 90 && g > 170 && b < 90 {
        Colour::Green
    } else if r > 170 && g < 90 && b < 90 {
        Colour::Red
    } else if r < 90 && g < 90 && b > 90 {
        Colour::Blue
    } else {
        Colour::Other
    }
}

struct Clip {
    name: &'static str,
    encoder: &'static str,
    extra: &'static str,
    w: u32,
    h: u32,
    format: Format,
}

// Quadrants (picture coordinates): top-left yellow, top-right green, bottom-left red, bottom-right dark blue. Drawn at
// 2x2 and scaled up without filtering.
fn quadrants(w: u32, h: u32) -> String {
    format!(
        "nullsrc=s=2x2:r=30:d=3,format=yuv444p,geq=lum='if(lt(Y,H/2),if(lt(X,W/2),210,145),if(lt(X,W/2),81,29))':\
         cb='if(lt(Y,H/2),if(lt(X,W/2),16,54),if(lt(X,W/2),90,240))':\
         cr='if(lt(Y,H/2),if(lt(X,W/2),146,34),if(lt(X,W/2),240,110))',scale={w}:{h}:flags=neighbor,setsar=1"
    )
}

fn sh(cmd: &str) -> bool {
    Command::new("sh")
        .arg("-c")
        .arg(cmd)
        .status()
        .is_ok_and(|s| s.success())
}

fn generate(ffmpeg: &str, c: &Clip, out: &str) -> bool {
    let audio = if out.ends_with(".webm") {
        "libopus"
    } else {
        "aac"
    };
    let cmd = format!(
        "'{ffmpeg}' -nostdin -hide_banner -loglevel error -y -f lavfi -i \"{}\" -f lavfi -i \"sine=frequency=440:duration=3\" \
         -shortest -pix_fmt yuv420p -g 30 -c:v {} {} -c:a {audio} '{out}' >/dev/null 2>&1",
        quadrants(c.w, c.h),
        c.encoder,
        c.extra
    );
    sh(&cmd) && Path::new(out).exists()
}

fn open(path: &str) -> OwnedFd {
    File::open(path).unwrap().into()
}

const OPTIONS: Options = Options {
    audio: false,
    hwdec: true,
    display: None,
};

fn report(p: &Player) -> Value {
    serde_json::from_str(&p.report()).unwrap_or(Value::Null)
}

fn wait_for(p: &mut Player, mut done: impl FnMut(&Player) -> bool, seconds: f64) -> bool {
    let end = Instant::now() + Duration::from_secs_f64(seconds);
    while Instant::now() < end {
        p.update();
        if done(p) {
            return true;
        }
        if std::env::var_os("XR_VIDEO_DEBUG").is_some() {
            eprintln!(
                "    pos {:.2} dur {:.2} state {} {}",
                p.position_s(),
                p.duration_s(),
                p.state().name(),
                p.report()
            );
        }
        std::thread::sleep(Duration::from_millis(8));
    }
    false
}

/// Display frames as the renderer makes them: update, (draw), swap report.
fn frames_until(p: &mut Player, mut done: impl FnMut(&Player) -> bool, seconds: f64) -> bool {
    let end = Instant::now() + Duration::from_secs_f64(seconds);
    while Instant::now() < end {
        p.update();
        if done(p) {
            return true;
        }
        p.report_swap();
        std::thread::sleep(Duration::from_millis(8));
    }
    false
}

/// `ffmpeg -i <clip> -c copy -f nut pipe:1` (web_video.relay_argv): the read end of its stdout.
fn relay(ffmpeg: &str, clip: &str) -> Option<(Child, OwnedFd)> {
    let mut child = Command::new(ffmpeg)
        .args([
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            clip,
            "-sn",
            "-dn",
            "-c",
            "copy",
            "-f",
            "nut",
            "pipe:1",
        ])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .ok()?;
    let out = child.stdout.take()?;
    Some((child, out.into()))
}

/// Colour at (u, v) of the texture (level `level`), v = 0 the top row.
fn texel(gl: &Gl, tex: glow::Texture, level: i32, x: i32, y: i32) -> Colour {
    unsafe {
        let fb = gl.create_framebuffer().unwrap();
        gl.bind_framebuffer(glow::FRAMEBUFFER, Some(fb));
        gl.framebuffer_texture_2d(
            glow::FRAMEBUFFER,
            glow::COLOR_ATTACHMENT0,
            glow::TEXTURE_2D,
            Some(tex),
            level,
        );
        let px = read(gl, x, y, 1, 1);
        gl.bind_framebuffer(glow::FRAMEBUFFER, None);
        gl.delete_framebuffer(fb);
        classify(&px)
    }
}

fn centre(gl: &Gl, p: &Player) -> Colour {
    texel(
        gl,
        p.texture().unwrap(),
        0,
        (p.width() / 4) as i32,
        (p.height() / 4) as i32,
    ) // top-left quadrant
}

fn decode(gl: &Gl, ffmpeg: &str, dir: &str) {
    if ffmpeg.is_empty() || !Path::new(ffmpeg).exists() {
        println!("decode: SKIP (no ffmpeg to generate clips)");
        return;
    }
    std::fs::create_dir_all(dir).unwrap();
    use Projection::*;
    use Stereo::*;
    let clips = [
        Clip {
            name: "h264-360-mono.mp4",
            encoder: "libx264",
            extra: "-preset ultrafast",
            w: 1024,
            h: 512,
            format: Format::default(),
        },
        Clip {
            name: "hevc-180-sbs.mkv",
            encoder: "libx265",
            extra: "-preset ultrafast -x265-params log-level=error",
            w: 1024,
            h: 512,
            format: Format::new(Equirect180, Sbs, 180),
        },
        Clip {
            name: "vp9-360-tb.webm",
            encoder: "libvpx-vp9",
            extra: "-deadline realtime -cpu-used 8",
            w: 512,
            h: 512,
            format: Format::new(Equirect360, Tb, 180),
        },
        Clip {
            name: "av1-fisheye-sbs.mkv",
            encoder: "libsvtav1",
            extra: "-preset 12",
            w: 1024,
            h: 512,
            format: Format::new(Fisheye, Sbs, 190),
        },
        // beyond H.264 hardware limits: software
        Clip {
            name: "h264-8k-360-mono.mp4",
            encoder: "libx264",
            extra: "-preset ultrafast",
            w: 7680,
            h: 3840,
            format: Format::default(),
        },
        Clip {
            name: "hevc-8k-180-sbs.mp4",
            encoder: "libx265",
            extra: "-preset ultrafast -tag:v hvc1 -x265-params log-level=error",
            w: 8192,
            h: 4096,
            format: Format::new(Equirect180, Sbs, 180),
        },
    ];
    let mut decoded = 0;
    for c in &clips {
        let path = format!("{dir}/{}", c.name);
        if !generate(ffmpeg, c, &path) {
            println!(
                "decode {}: SKIP (encoder {} unavailable)",
                c.name, c.encoder
            );
            continue;
        }
        let mut player = match Player::open(gl.clone(), open(&path), OPTIONS) {
            Ok(p) => p,
            Err(e) => {
                check!(false);
                eprintln!("  {}: {e}", c.name);
                continue;
            }
        };
        let playing = wait_for(
            &mut player,
            |p| p.texture().is_some() && p.state() == State::Playing,
            20.0,
        );
        check!(playing);
        if !playing {
            eprintln!(
                "  {}: state {} {}",
                c.name,
                player.state().name(),
                player.error()
            );
            continue;
        }
        let (tw, th) = (player.width() as i32, player.height() as i32);
        check!(tw == c.w.min(8192) as i32 && th == c.h.min(8192) as i32);
        // Read the decoded frame back: texture row t = 0 must be the picture's top (flip_y() false).
        let tex = player.texture().unwrap();
        let probe = |u: f32, v: f32| {
            let y = if player.flip_y() {
                ((1.0 - v) * th as f32) as i32
            } else {
                (v * th as f32) as i32
            };
            texel(gl, tex, 0, (u * tw as f32) as i32, y)
        };
        let q = [
            probe(0.25, 0.25),
            probe(0.75, 0.25),
            probe(0.25, 0.75),
            probe(0.75, 0.75),
        ];
        let upright = q == [Colour::Yellow, Colour::Green, Colour::Red, Colour::Blue];
        if !upright {
            eprintln!(
                "  {}: tl {:?} tr {:?} bl {:?} br {:?}",
                c.name, q[0], q[1], q[2], q[3]
            );
        }
        check!(upright);
        // The mip chain is regenerated from each new picture: level 2 shows the same quadrants.
        let probe2 = |u: f32, v: f32| {
            texel(
                gl,
                tex,
                2,
                (u * (tw >> 2) as f32) as i32,
                (v * (th >> 2) as f32) as i32,
            )
        };
        let mipped = [
            probe2(0.25, 0.25),
            probe2(0.75, 0.25),
            probe2(0.25, 0.75),
            probe2(0.75, 0.75),
        ] == [Colour::Yellow, Colour::Green, Colour::Red, Colour::Blue];
        if !mipped {
            eprintln!("  {}: mip level 2 does not show the picture", c.name);
        }
        check!(mipped);
        // Through the projection pass: looking up-left lands in the top-left quadrant of the eye's image for 360°.
        if c.format.projection == Equirect360 {
            let sphere = Sphere::new(gl.clone()).unwrap();
            let _t = Target::new(gl, 64, 64);
            unsafe { gl.viewport(0, 0, 64, 64) };
            sphere.draw(
                tex,
                player.flip_y(),
                &c.format,
                0,
                &IDENTITY,
                1.0,
                1.0,
                false,
            );
            let seen = classify(&read(gl, 12, 52, 1, 1)); // upper left of the view
            if seen != Colour::Yellow {
                eprintln!("  {} projected: {seen:?}", c.name);
            }
            check!(if c.format.stereo == Tb {
                seen == Colour::Yellow || seen == Colour::Green
            } else {
                seen == Colour::Yellow
            });
        }
        // Controls: pause, seek, end.
        player.toggle_pause();
        check!(wait_for(&mut player, |p| p.state() == State::Paused, 5.0));
        check!(report(&player)["readback"]["paused"] == true);
        player.toggle_pause();
        check!(wait_for(&mut player, |p| p.state() == State::Playing, 5.0));
        {
            let observed = &report(&player)["readback"];
            check!(observed["paused"] == false);
            check!(observed["duration_s"].is_number());
        }
        check!(player.duration_s() > 2.5 && player.duration_s() < 3.5);
        let before = player.position_s();
        player.seek(1.0);
        check!(wait_for(
            &mut player,
            |p| p.position_s() >= before + 0.9,
            5.0
        ));
        {
            let observed = &report(&player)["readback"];
            check!(
                observed["position_s"]
                    .as_f64()
                    .is_some_and(|v| v >= before + 0.9)
            );
            check!(observed["command_errors"] == 0);
        }
        // Plays to the end: keep-open holds the last frame and reports ended; seeking back plays again.
        check!(wait_for(&mut player, |p| p.state() == State::Ended, 10.0));
        check!(player.texture().is_some());
        player.seek(-10.0);
        check!(wait_for(
            &mut player,
            |p| p.position_s() < 1.0 && p.state() != State::Ended,
            10.0
        ));
        let report = player.report();
        // Zero copy: with the context's render node known, mpv's vaapi interop imports the decoded surfaces;
        // vaapi-copy would mean every frame went through system memory. Software decoding ("no") is fine.
        check!(
            report.contains("\"va_device\":\"default\"")
                || !report.contains("\"hwdec\":\"vaapi-copy\"")
        );
        check!(report.contains("\"early\":0,")); // never more than one display interval early
        println!(
            "decode {}: {}x{} -> {tw}x{th} texture, {report}",
            c.name, c.w, c.h
        );
        decoded += 1;
    }
    // A web video as the run relays it: `ffmpeg -i <stream> -c copy -f nut pipe:1` into a pipe.
    let clip = format!("{dir}/h264-360-mono.mp4");
    match Path::new(&clip)
        .exists()
        .then(|| relay(ffmpeg, &clip))
        .flatten()
    {
        None => println!("decode relay-pipe: SKIP (no clip or ffmpeg)"),
        Some((mut child, fd)) => {
            match Player::open(gl.clone(), fd, OPTIONS) {
                Err(e) => {
                    check!(false);
                    eprintln!("  relay: {e}");
                }
                Ok(mut player) => {
                    check!(wait_for(
                        &mut player,
                        |p| p.texture().is_some() && p.state() == State::Playing,
                        20.0
                    ));
                    check!(player.width() == 1024 && player.height() == 512);
                    if let Some(tex) = player.texture() {
                        check!(texel(gl, tex, 0, 256, 128) == Colour::Yellow); // upright: the top-left quadrant
                    }
                    player.toggle_pause();
                    check!(wait_for(&mut player, |p| p.state() == State::Paused, 5.0));
                    player.toggle_pause();
                    check!(wait_for(&mut player, |p| p.state() == State::Ended, 15.0)); // end of the pipe: held
                    check!(player.texture().is_some() && player.position_s() > 2.0);
                    println!("decode relay-pipe (nut): {}", player.report());
                }
            }
            check!(child.wait().is_ok_and(|s| s.success()));
        }
    }
    // Not a video: reported as an error, never a crash.
    let junk = format!("{dir}/not-a-video.mp4");
    std::fs::write(&junk, "this is not a video file\n").unwrap();
    match Player::open(gl.clone(), open(&junk), OPTIONS) {
        Err(e) => {
            check!(false);
            eprintln!("  not-a-video: {e}");
        }
        Ok(mut player) => {
            check!(wait_for(&mut player, |p| p.state() == State::Error, 10.0));
            check!(!player.error().is_empty() && player.texture().is_none());
            let failure = report(&player);
            check!(failure["state"] == "error");
            check!(failure["error"].as_str().is_some_and(|s| !s.is_empty()));
            println!("decode not-a-video: error \"{}\"", player.error());
        }
    }
    check!(decoded >= 1);
}

// 5. reopen() (a web video's seek, another video while one plays) and closing a playing video.
fn reuse_and_close(gl: &Gl, ffmpeg: &str, dir: &str) {
    if ffmpeg.is_empty() || !Path::new(ffmpeg).exists() {
        println!("reuse: SKIP (no ffmpeg to generate clips)");
        return;
    }
    let (quads, blue_clip) = (
        format!("{dir}/h264-360-mono.mp4"),
        format!("{dir}/h264-blue.mp4"),
    );
    let cmd = format!(
        "'{ffmpeg}' -nostdin -hide_banner -loglevel error -y -f lavfi -i color=c=0x1830d0:s=1024x512:r=30:d=3 \
         -f lavfi -i sine=frequency=440:duration=3 -shortest -pix_fmt yuv420p -c:v libx264 -preset ultrafast -c:a aac \
         '{blue_clip}' >/dev/null 2>&1"
    );
    if !Path::new(&quads).exists() || !sh(&cmd) {
        println!("reuse: SKIP (no clips)");
        return;
    }
    let Ok(mut player) = Player::open(gl.clone(), open(&quads), OPTIONS) else {
        check!(false);
        return;
    };
    let started =
        |p: &Player| p.texture().is_some() && p.state() == State::Playing && p.position_s() > 0.5;
    check!(frames_until(&mut player, started, 20.0));
    check!(centre(gl, &player) == Colour::Yellow);
    // The next file in the same player: nothing until its own first frame, which is the new picture, from its start.
    check!(player.reopen(open(&blue_clip), OPTIONS).is_ok());
    check!(player.texture().is_none() && player.state() == State::Loading);
    let mut first = Colour::Other;
    check!(frames_until(
        &mut player,
        |p| {
            if p.texture().is_some() && first == Colour::Other {
                first = centre(gl, p);
            }
            p.texture().is_some()
        },
        20.0
    ));
    check!(first == Colour::Blue); // no frame of the previous file shown
    check!(frames_until(
        &mut player,
        |p| p.state() == State::Playing,
        10.0
    ));
    check!(player.position_s() < 1.5 && player.duration_s() > 2.5);
    // Play/pause pressed while the reopen waits for mpv: the next file starts paused, as a new player would.
    check!(player.reopen(open(&blue_clip), OPTIONS).is_ok());
    player.toggle_pause();
    check!(frames_until(
        &mut player,
        |p| p.texture().is_some() && p.state() == State::Paused,
        20.0
    ));
    // Reopened while paused: plays (a new player would), and from a relay pipe as a web seek does.
    let child = match relay(ffmpeg, &quads) {
        Some((child, fd)) => {
            check!(player.reopen(fd, OPTIONS).is_ok());
            Some(child)
        }
        None => {
            check!(false);
            None
        }
    };
    check!(frames_until(
        &mut player,
        |p| p.texture().is_some() && p.state() == State::Playing,
        20.0
    ));
    check!(centre(gl, &player) == Colour::Yellow);
    let rep = player.report();
    check!(rep.contains("\"reopens\":3,"));
    println!("reuse: {rep}");
    // Closing a playing video: before, mpv_render_context_free sat out mpv's 200 ms wait for the frame it handed over.
    let mut first = Some(player);
    let mut fastest = f64::MAX;
    for _ in 0..3 {
        let p = first.take().unwrap_or_else(|| {
            let mut p = Player::open(gl.clone(), open(&quads), OPTIONS).unwrap();
            check!(frames_until(&mut p, started, 20.0));
            p
        });
        let t0 = Instant::now();
        drop(p);
        fastest = fastest.min(t0.elapsed().as_secs_f64() * 1e3);
    }
    check!(fastest < 150.0);
    println!("close while playing: {fastest:.1} ms (fastest of 3)");
    if let Some(mut c) = child {
        let _ = c.wait();
    }
    // A failed player is not reused: the caller keeps its descriptor and opens a new player.
    let junk = format!("{dir}/not-a-video.mp4");
    let mut broken = Player::open(gl.clone(), open(&junk), OPTIONS).unwrap();
    check!(wait_for(&mut broken, |p| p.state() == State::Error, 10.0));
    match broken.reopen(open(&quads), OPTIONS) {
        Ok(()) => check!(false),
        Err(fd) => check!(unsafe { libc::fcntl(fd.as_raw_fd(), libc::F_GETFD) } >= 0), // handed back untouched
    }
}

fn mag_filter(gl: &Gl) -> i32 {
    // The magnification filter; minification always stays trilinear (the player's mipmaps), whatever the setting.
    unsafe {
        let m = gl.get_tex_parameter_i32(glow::TEXTURE_2D, glow::TEXTURE_MIN_FILTER);
        if m == glow::LINEAR_MIPMAP_LINEAR as i32 {
            gl.get_tex_parameter_i32(glow::TEXTURE_2D, glow::TEXTURE_MAG_FILTER)
        } else {
            -1
        }
    }
}

// 6. Quality settings through the renderer's Mode, as the `quality` frame applies them.
fn quality_live(gl: &Gl, dir: &str) {
    let quads = format!("{dir}/h264-360-mono.mp4");
    if !Path::new(&quads).exists() {
        println!("quality: SKIP (no clip)");
        return;
    }
    let mut mode = Mode::new(gl.clone());
    check!(mode.quality() == Quality::default()); // Catmull-Rom, no deband (what the renderer does without a frame)
    check!(mode.quality().filter == Filter::CatmullRom);
    let t = Target::new(gl, 64, 64);
    // As the compositor: prepare (mpv leaves framebuffer 0 bound), bind the output, draw, swap.
    let frame = |mode: &mut Mode| {
        mode.prepare();
        t.bind();
        mode.draw(&IDENTITY, 46.0, 64, 64, None);
        mode.after_swap();
    };
    let until_playing = |mode: &mut Mode| {
        for _ in 0..2500 {
            frame(mode);
            if mode.state() == State::Playing {
                return true;
            }
            std::thread::sleep(Duration::from_millis(4));
        }
        false
    };
    mode.open(open(&quads), Format::default(), false);
    check!(until_playing(&mut mode));
    frame(&mut mode);
    check!(mag_filter(gl) == glow::LINEAR as i32);
    check!(mode.report().contains("\"filter\":\"catmull-rom\""));
    for f in [Filter::Sharp, Filter::Bilinear] {
        // the cubic ones and bilinear share GL_LINEAR
        mode.set_quality(Quality {
            filter: f,
            deband: false,
        });
        frame(&mut mode);
        check!(mag_filter(gl) == glow::LINEAR as i32);
        check!(
            mode.report()
                .contains(&format!("\"filter\":\"{}\"", f.name()))
        );
    }
    mode.set_quality(Quality {
        filter: Filter::Nearest,
        deband: true,
    }); // live: the next drawn frame
    frame(&mut mode);
    check!(mag_filter(gl) == glow::NEAREST as i32);
    for _ in 0..30 {
        frame(&mut mode);
        std::thread::sleep(Duration::from_millis(4));
    }
    check!(mode.state() == State::Playing); // deband on did not break playback
    let rep = mode.report();
    check!(rep.contains(
        "\"filter\":\"nearest\",\"forced\":false,\"setting\":\"nearest\",\"deband\":true"
    ));
    check!(rep.contains("\"quality_errors\":0")); // mpv took `deband` (each open sets it too)
    check!(!rep.contains("\"quality_set\":0"));
    {
        let r: Value = serde_json::from_str(&rep).unwrap_or(Value::Null);
        check!(r["quality_readback"]["filter_applied"] == "nearest");
        check!(r["quality_readback"]["deband_applied"] == true);
        check!(r["playback"]["command_errors"] == 0);
        check!(r["format_applied"]["projection"] == "360");
    }
    mode.close();
    mode.open(open(&quads), Format::default(), false); // kept for the next video
    check!(until_playing(&mut mode));
    frame(&mut mode);
    check!(mag_filter(gl) == glow::NEAREST as i32);
    mode.set_quality(Quality::default());
    frame(&mut mode);
    check!(mag_filter(gl) == glow::LINEAR as i32);
    mode.close();
    println!("quality: filter and deband applied live");
}

// 7. What goes behind a video, through Mode::draw: green here, one viewport at a time (a clear ignores the viewport).
fn background_behind(gl: &Gl, dir: &str) {
    let clip = format!("{dir}/h264-360-mono.mp4");
    if !Path::new(&clip).exists() {
        println!("background: SKIP (no clip)");
        return;
    }
    let mut mode = Mode::new(gl.clone());
    const W: i32 = 192;
    const H: i32 = 64; // 3:1, the glasses' side-by-side mode: two output eyes
    let t = Target::new(gl, W, H);
    let calls = std::cell::Cell::new(0);
    let green = |half_w: f32, half_h: f32| {
        calls.set(calls.get() + 1);
        check!(half_w > 0.0 && half_h > 0.0);
        unsafe {
            let mut v = [0i32; 4];
            gl.get_parameter_i32_slice(glow::VIEWPORT, &mut v);
            gl.enable(glow::SCISSOR_TEST);
            gl.scissor(v[0], v[1], v[2], v[3]);
            gl.clear_color(0.0, 1.0, 0.0, 1.0);
            gl.clear(glow::COLOR_BUFFER_BIT);
            gl.disable(glow::SCISSOR_TEST);
        }
        true
    };
    let frame = |mode: &mut Mode, view: &Mat3| {
        mode.prepare();
        calls.set(0);
        t.bind();
        mode.draw(view, 46.0, W as u32, H as u32, Some(&green));
        mode.after_swap();
    };
    let is_green = |x, y| {
        let p = read(gl, x, y, 1, 1);
        p[0] == 0 && p[1] == 255 && p[2] == 0
    };
    frame(&mut mode, &AHEAD); // nothing open: the background alone, in both eyes
    check!(calls.get() == 2 && is_green(W / 4, H / 2) && is_green(3 * W / 4, H / 2));
    mode.open(open(&clip), Format::default(), false); // 360 degrees: it covers everything
    let mut playing = false;
    for _ in 0..2500 {
        frame(&mut mode, &AHEAD);
        playing = mode.state() == State::Playing;
        if playing {
            break;
        }
        std::thread::sleep(Duration::from_millis(4));
    }
    check!(playing);
    frame(&mut mode, &BEHIND);
    check!(calls.get() == 0 && !is_green(W / 4, H / 2) && !is_green(3 * W / 4, H / 2));
    mode.set_format(Format {
        projection: Projection::Equirect180,
        ..Format::default()
    });
    frame(&mut mode, &AHEAD);
    {
        let r: Value = serde_json::from_str(&mode.report()).unwrap_or(Value::Null);
        check!(r["format_applied"]["projection"] == "180");
        check!(r["format_applied"]["stereo"] == "mono");
        check!(r["format_applied"]["fov"] == 180);
    }
    // ahead: the picture, drawn over the background in each eye
    check!(calls.get() == 2 && !is_green(W / 4, H / 2) && !is_green(3 * W / 4, H / 2));
    frame(&mut mode, &BEHIND); // turned round, past the picture's edge: the background, in each eye
    check!(calls.get() == 2 && is_green(W / 4, H / 2) && is_green(3 * W / 4, H / 2));
    mode.close();
    println!(
        "background: shown behind a 180 degree picture, in each eye, and skipped behind a 360 degree one"
    );
}

fn main() {
    let args: Vec<String> = std::env::args()
        .skip(1)
        .filter(|a| !a.starts_with("--"))
        .collect();
    let ffmpeg = args
        .first()
        .cloned()
        .unwrap_or_else(|| "/usr/bin/ffmpeg".into());
    let dir = args
        .get(1)
        .cloned()
        .unwrap_or_else(|| concat!(env!("CARGO_TARGET_TMPDIR"), "/video-samples").to_owned());
    let headless = match Headless::new(None) {
        Ok(h) => h,
        Err(e) => {
            println!("SKIP: no surfaceless EGL / GLES 3 context ({e})");
            std::process::exit(77);
        }
    };
    let gl = headless.gl.clone();
    unsafe {
        println!(
            "GL: {} / {}",
            gl.get_parameter_string(glow::RENDERER),
            gl.get_parameter_string(glow::VERSION)
        );
    }
    projection_matches_cpu(&gl);
    sampling(&gl);
    decode(&gl, &ffmpeg, &dir);
    reuse_and_close(&gl, &ffmpeg, &dir);
    quality_live(&gl, &dir);
    background_behind(&gl, &dir);
    drop(gl);
    drop(headless);
    let failures = FAILURES.load(Ordering::Relaxed);
    if failures > 0 {
        eprintln!("{failures} failure(s)");
        std::process::exit(1);
    }
    println!("video_render: ok");
}
