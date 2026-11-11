//! The present does not wait for the GPU: `draw_gpu` copies the frame into a swapchain dmabuf, flushes and commits, and
//! the compositor's read of that buffer waits for the draw through the dmabuf's implicit fence, as it does for the C++
//! renderer's eglSwapBuffers (Mesa's Wayland EGL flushes, it does not finish). This checks that ordering across
//! processes, with nothing faked: this process draws a frame slow enough to still be on the GPU, copies it into a
//! dmabuf with `GlCompositor::present_into`, flushes, and hands the buffer to a child process (the compositor's place)
//! at once. The child imports the dmabuf on its own EGL display and reads it back. Each frame has its own colour, so a
//! read that ran ahead of the draw shows the previous frame (stale) or the cleared buffer (torn), never this one.
//!
//! The check counts only if the frame was still in flight at the hand-off (a fence after the flush not yet signalled),
//! so it fails, rather than passing vacuously, if the GPU finished every frame before the child was told. Exit 77
//! (skipped) without EGL/GLES, a render node or dmabuf import.

use std::io::{BufRead, BufReader, Write};
use std::os::fd::AsRawFd;
use std::os::unix::fs::MetadataExt;
use std::os::unix::process::CommandExt;
use std::process::{Command, Stdio};

use glow::HasContext;
use xr_renderer::capture::{DRM_XRGB8888, MOD_INVALID, MOD_LINEAR};
use xr_renderer::dmabuf::Allocator;
use xr_renderer::egl::{render_node, DmabufPlane};
use xr_renderer::gl::{compile_program, GlCompositor};

const W: u32 = 1536; // the 1920x1200 @1.25 glasses surface
const H: u32 = 960;
const FRAMES: usize = 24;
const CHILD: &str = "XR_PRESENT_SYNC_CHILD"; // set in the child: "<node> <width> <height> <fourcc> <offset> <stride> <modifier>"
const CHILD_FD: i32 = 3;

fn skip(why: &str) -> ! {
    println!("SKIP: {why}");
    std::process::exit(77);
}

/// The colour of frame `i`: distinct from the previous frame's and from the cleared (black) buffer.
fn colour(i: usize) -> [u8; 3] {
    [
        (40 + i * 37 % 200) as u8,
        (200 - i * 53 % 160) as u8,
        (30 + i * 71 % 210) as u8,
    ]
}

fn main() {
    match std::env::var(CHILD) {
        Ok(desc) => child(&desc),
        Err(_) => parent(),
    }
}

fn parent() {
    let gl = match GlCompositor::create_offscreen(W, H, 1) {
        Ok(gl) => gl,
        Err(why) => skip(&format!("no surfaceless EGL / GLES context: {why}")),
    };
    let raw = gl.gl().raw();
    let Some(node) = render_node(&raw) else {
        skip("no render node for this context")
    };
    let Ok(meta) = std::fs::metadata(&node) else {
        skip("render node stat failed")
    };
    let alloc = match Allocator::open(meta.rdev()) {
        Ok(a) => a,
        Err(why) => skip(why),
    };
    // The renderer's own choice for its swapchain (CaptureState::start).
    let modifier = if gl.dmabuf_importable(DRM_XRGB8888, MOD_LINEAR) {
        MOD_LINEAR
    } else {
        MOD_INVALID
    };
    let buf = match alloc.allocate(W, H, DRM_XRGB8888, modifier, false) {
        Ok(b) => b,
        Err(why) => skip(why),
    };
    let plane = DmabufPlane {
        fd: buf.fd.as_raw_fd(),
        width: W,
        height: H,
        fourcc: buf.fourcc,
        offset: buf.offset,
        stride: buf.stride,
        modifier: buf.modifier,
    };
    let Some(image) = gl.import_dmabuf(&plane) else {
        skip("dmabuf import failed")
    };
    let Some(rb) = gl.image_renderbuffer(&image) else {
        skip("renderbuffer from dmabuf failed")
    };

    // A frame slow enough to still be on the GPU when the child reads: every fragment loops before writing its colour.
    let program = compile_program(
        &raw,
        "#version 300 es\nvoid main() { vec2 p = vec2(gl_VertexID == 1 ? 3.0 : -1.0, gl_VertexID == 2 ? 3.0 : -1.0);\n\
         gl_Position = vec4(p, 0.0, 1.0); }\n",
        "#version 300 es\nprecision highp float; uniform vec3 colour; uniform int spins; out vec4 frag;\n\
         void main() { float a = gl_FragCoord.x * 1e-4;\n\
         for (int i = 0; i < spins; i++) a = fract(sin(a * 12.9898 + float(i)) * 43758.5453);\n\
         frag = a > 2.0 ? vec4(1.0, 0.0, 1.0, 1.0) : vec4(colour, 1.0); }\n",
    )
    .expect("the slow program builds");
    // SAFETY (all GL here): plain GL calls on the compositor's context, current on this thread for the whole test.
    let (colour_at, spins_at) = unsafe {
        (
            raw.get_uniform_location(program, "colour"),
            raw.get_uniform_location(program, "spins"),
        )
    };

    let exe = std::env::current_exe().expect("the test binary");
    let desc = format!(
        "{node} {W} {H} {} {} {} {}",
        buf.fourcc, buf.offset, buf.stride, buf.modifier
    );
    let fd = buf.fd.as_raw_fd();
    let mut cmd = Command::new(exe);
    cmd.env(CHILD, desc)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped());
    // SAFETY: dup2 is async-signal-safe; the child gets the dmabuf at CHILD_FD (dup2 leaves it without CLOEXEC).
    unsafe {
        cmd.pre_exec(move || {
            if libc::dup2(fd, CHILD_FD) < 0 {
                Err(std::io::Error::last_os_error())
            } else {
                Ok(())
            }
        });
    }
    let mut child = cmd.spawn().expect("the consumer process");
    let mut to = child.stdin.take().unwrap();
    let mut from = BufReader::new(child.stdout.take().unwrap());
    let mut line = String::new();
    from.read_line(&mut line).unwrap();
    if line.trim() != "ready" {
        let _ = child.wait();
        skip(&format!(
            "the consumer could not import the dmabuf: {}",
            line.trim()
        ));
    }

    // SAFETY: as above; the compositor's framebuffer is bound (present_into leaves it bound).
    let draw = |c: [u8; 3], spins: i32| unsafe {
        raw.viewport(0, 0, W as i32, H as i32);
        raw.use_program(Some(program));
        raw.uniform_3_f32(
            colour_at.as_ref(),
            c[0] as f32 / 255.0,
            c[1] as f32 / 255.0,
            c[2] as f32 / 255.0,
        );
        raw.uniform_1_i32(spins_at.as_ref(), spins);
        raw.draw_arrays(glow::TRIANGLES, 0, 3);
    };
    // About 10 ms of GPU work per frame, whatever this GPU's speed.
    let mut spins = 16;
    while spins < 1 << 20 {
        let t = std::time::Instant::now();
        draw([0, 0, 0], spins);
        gl.finish();
        if t.elapsed().as_secs_f64() * 1e3 >= 10.0 {
            break;
        }
        spins *= 2;
    }

    let (mut in_flight, mut wrong) = (0usize, 0usize);
    for i in 0..FRAMES {
        let c = colour(i);
        draw(c, spins);
        // SAFETY: as above.
        let fence = unsafe {
            assert!(gl.present_into(rb), "present_into");
            let fence = raw.fence_sync(glow::SYNC_GPU_COMMANDS_COMPLETE, 0).unwrap();
            gl.gl().flush(); // the present: no finish
            fence
        };
        writeln!(to, "{i}").unwrap(); // the commit: the compositor may read the buffer from here
                                      // SAFETY: as above.
        let busy = unsafe { raw.client_wait_sync(fence, 0, 0) } == glow::TIMEOUT_EXPIRED;
        in_flight += usize::from(busy);
        line.clear();
        from.read_line(&mut line).unwrap(); // the release: the child read the buffer
        let got: Vec<u8> = line
            .split_whitespace()
            .filter_map(|v| v.parse().ok())
            .collect(); // "torn": none
        if got != c {
            eprintln!("frame {i}: the consumer read {got:?}, the frame is {c:?} (in flight at the commit: {busy})");
            wrong += 1;
        }
        // SAFETY: as above; the fence is done once the child's read (ordered after it) returned.
        unsafe {
            raw.client_wait_sync(fence, glow::SYNC_FLUSH_COMMANDS_BIT, u64::MAX as i32);
            raw.delete_sync(fence);
        }
    }
    drop(to);
    let status = child.wait().unwrap();
    gl.delete_renderbuffer(rb);
    gl.destroy_image(image);
    println!("present_sync: {FRAMES} frames of {spins} spins, {in_flight} still on the GPU at the commit, {wrong} read wrong");
    assert!(status.success(), "the consumer failed: {status}");
    assert_eq!(wrong, 0, "a frame was read before its draw finished");
    assert!(
        in_flight >= FRAMES / 2,
        "too few frames in flight at the commit for the check to mean anything"
    );
}

/// The compositor's side: import the dmabuf at CHILD_FD on its own EGL display and read the frame on each commit.
fn child(desc: &str) {
    let v: Vec<&str> = desc.split(' ').collect();
    let n = |i: usize| v[i].parse::<u64>().unwrap();
    let mut out = std::io::stdout().lock();
    let gl = match GlCompositor::create_on(Some(v[0]), 16, 16, 1) {
        Ok(gl) => gl,
        Err(why) => {
            writeln!(out, "no context: {why}").unwrap();
            return;
        }
    };
    let plane = DmabufPlane {
        fd: CHILD_FD,
        width: n(1) as u32,
        height: n(2) as u32,
        fourcc: n(3) as u32,
        offset: n(4) as u32,
        stride: n(5) as u32,
        modifier: n(6),
    };
    let (Some(image), raw) = (gl.import_dmabuf(&plane), gl.gl().raw()) else {
        writeln!(out, "import failed").unwrap();
        return;
    };
    let Some(rb) = gl.image_renderbuffer(&image) else {
        writeln!(out, "no renderbuffer").unwrap();
        return;
    };
    // SAFETY (all GL here): plain GL calls on this process's context, current on this thread.
    let fb = unsafe {
        let fb = raw.create_framebuffer().unwrap();
        raw.bind_framebuffer(glow::FRAMEBUFFER, Some(fb));
        raw.framebuffer_renderbuffer(
            glow::FRAMEBUFFER,
            glow::COLOR_ATTACHMENT0,
            glow::RENDERBUFFER,
            Some(rb),
        );
        fb
    };
    writeln!(out, "ready").unwrap();
    out.flush().unwrap();
    let (w, h) = (plane.width as i32, plane.height as i32);
    for _ in std::io::stdin().lock().lines().map_while(Result::ok) {
        let mut px = [0u8; 4];
        let mut rgb = [0u8; 3];
        let mut agree = true;
        // Three points of the frame: the first row, the middle and the last row (the copy is flipped).
        for (k, (x, y)) in [(1, 1), (w / 2, h / 2), (w - 2, h - 2)]
            .into_iter()
            .enumerate()
        {
            // SAFETY: as above; `px` holds one RGBA pixel.
            unsafe {
                raw.read_pixels(
                    x,
                    y,
                    1,
                    1,
                    glow::RGBA,
                    glow::UNSIGNED_BYTE,
                    glow::PixelPackData::Slice(Some(&mut px)),
                );
            }
            if k == 0 {
                rgb.copy_from_slice(&px[..3]);
            }
            agree &= px[..3] == rgb;
        }
        if agree {
            writeln!(out, "{} {} {}", rgb[0], rgb[1], rgb[2]).unwrap();
        } else {
            writeln!(out, "torn").unwrap();
        }
        out.flush().unwrap();
    }
    // SAFETY: as above.
    unsafe { raw.delete_framebuffer(fb) };
    gl.delete_renderbuffer(rb);
    gl.destroy_image(image);
}
