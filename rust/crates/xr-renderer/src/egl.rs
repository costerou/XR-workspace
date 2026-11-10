//! EGL helpers: a headless GLES context for tests and benches, the GL loader mpv and glow use, and the render node of
//! the GPU behind the current context (spike/src/video_player.cpp egl_render_node).

use std::ffi::{CStr, CString, c_char, c_void};
use std::rc::Rc;

use khronos_egl as egl;

const PLATFORM_SURFACELESS_MESA: egl::Enum = 0x31DD;
const PLATFORM_DEVICE_EXT: egl::Enum = 0x313F;
const DRM_RENDER_NODE_FILE_EXT: egl::Int = 0x3377;
const DEVICE_UUID_EXT: egl::Int = 0x335C;
const GL_DEVICE_UUID_EXT: u32 = 0x9597;
const UUID_SIZE: usize = 16;
const MAX_DEVICES: usize = 16;

type Device = *mut c_void;
type QueryDevices = unsafe extern "system" fn(i32, *mut Device, *mut i32) -> u32;
type QueryDeviceString = unsafe extern "system" fn(Device, i32) -> *const c_char;
type QueryDeviceBinary = unsafe extern "system" fn(Device, i32, i32, *mut c_void, *mut i32) -> u32;
type GetUnsignedBytei = unsafe extern "system" fn(u32, u32, *mut u8);

#[link(name = "EGL")]
unsafe extern "C" {
    fn eglGetProcAddress(name: *const c_char) -> *mut c_void;
}

/// A GL or EGL entry point by name, null if missing. Valid as mpv's `get_proc_address`.
pub fn proc_address(name: &CStr) -> *mut c_void {
    // SAFETY: name is NUL-terminated; eglGetProcAddress only reads it.
    unsafe { eglGetProcAddress(name.as_ptr()) }
}

fn proc<T: Copy>(name: &str) -> Option<T> {
    debug_assert_eq!(size_of::<T>(), size_of::<*mut c_void>());
    let name = CString::new(name).ok()?;
    let p = proc_address(&name);
    // SAFETY: T is the extern fn pointer type of `name` (callers pair them), same size as a data pointer.
    (!p.is_null()).then(|| unsafe { std::mem::transmute_copy::<*mut c_void, T>(&p) })
}

/// glow over the current context's GL entry points.
///
/// # Safety
/// A GLES context must be current on this thread for as long as the returned context is used.
pub unsafe fn load_gl() -> glow::Context {
    // SAFETY: the caller keeps a context current; proc_address returns its entry points.
    unsafe {
        glow::Context::from_loader_function(|s| {
            CString::new(s)
                .map(|n| proc_address(&n) as *const c_void)
                .unwrap_or(std::ptr::null())
        })
    }
}

fn has_token(list: Option<&CStr>, token: &str) -> bool {
    list.and_then(|l| l.to_str().ok())
        .is_some_and(|l| l.split(' ').any(|t| t == token))
}

struct Devices {
    list: Vec<Device>,
    string: QueryDeviceString,
    binary: Option<QueryDeviceBinary>,
}

fn devices() -> Option<Devices> {
    let egl = egl::Instance::new(egl::Static);
    let client = egl.query_string(None, egl::EXTENSIONS).ok();
    if !has_token(client, "EGL_EXT_device_enumeration") && !has_token(client, "EGL_EXT_device_base")
    {
        return None;
    }
    let query: QueryDevices = proc("eglQueryDevicesEXT")?;
    let string: QueryDeviceString = proc("eglQueryDeviceStringEXT")?;
    let mut list = vec![std::ptr::null_mut(); MAX_DEVICES];
    let mut count = 0;
    // SAFETY: list has MAX_DEVICES slots.
    if unsafe { query(MAX_DEVICES as i32, list.as_mut_ptr(), &mut count) } == 0 {
        return None;
    }
    list.truncate(count.clamp(0, MAX_DEVICES as i32) as usize);
    Some(Devices {
        list,
        string,
        binary: proc("eglQueryDeviceBinaryEXT"),
    })
}

impl Devices {
    fn string(&self, d: Device, name: i32) -> Option<&CStr> {
        // SAFETY: d came from eglQueryDevicesEXT; the result is null or a static NUL-terminated string.
        let p = unsafe { (self.string)(d, name) };
        // SAFETY: non-null results are NUL-terminated and live as long as the EGL library.
        (!p.is_null()).then(|| unsafe { CStr::from_ptr(p) })
    }

    fn node(&self, d: Device) -> Option<String> {
        if !has_token(
            self.string(d, egl::EXTENSIONS),
            "EGL_EXT_device_drm_render_node",
        ) {
            return None;
        }
        let node = self.string(d, DRM_RENDER_NODE_FILE_EXT)?.to_str().ok()?;
        (node.starts_with("/dev/dri/renderD") && node.len() <= 32).then(|| node.to_owned())
    }

    fn uuid(&self, d: Device) -> Option<[u8; UUID_SIZE]> {
        let binary = self.binary?;
        if !has_token(
            self.string(d, egl::EXTENSIONS),
            "EGL_EXT_device_persistent_id",
        ) {
            return None;
        }
        let mut id = [0u8; UUID_SIZE];
        let mut size = 0;
        // SAFETY: id has UUID_SIZE writable bytes.
        let ok = unsafe {
            binary(
                d,
                DEVICE_UUID_EXT,
                UUID_SIZE as i32,
                id.as_mut_ptr().cast(),
                &mut size,
            )
        };
        (ok != 0 && size == UUID_SIZE as i32).then_some(id)
    }
}

/// The render node of the GPU the current GL context draws on; None if that cannot be told for sure. The GPU is found
/// by its UUID (GL_EXT_memory_object against EGL_EXT_device_persistent_id): under PRIME the EGL display's own device is
/// not it. Without UUIDs only a machine with one GPU is unambiguous.
pub fn render_node(gl: &glow::Context) -> Option<String> {
    use glow::HasContext;
    let egl = egl::Instance::new(egl::Static);
    egl.get_current_context()?;
    let devices = devices()?;
    let gl_uuid: Option<GetUnsignedBytei> = proc("glGetUnsignedBytei_vEXT");
    if let (true, Some(gl_uuid), Some(_)) = (
        gl.supported_extensions().contains("GL_EXT_memory_object"),
        gl_uuid,
        devices.binary,
    ) {
        let mut uuid = [0u8; UUID_SIZE];
        // SAFETY: a context is current; uuid has GL_UUID_SIZE_EXT bytes.
        unsafe { gl_uuid(GL_DEVICE_UUID_EXT, 0, uuid.as_mut_ptr()) };
        let mut error = false;
        // SAFETY: plain GL query on the current context. Never leave a GL error for the compositor.
        while unsafe { gl.get_error() } != glow::NO_ERROR {
            error = true;
        }
        if !error {
            // The context's GPU is not among EGL's devices: do not guess.
            return devices
                .list
                .iter()
                .find(|&&d| devices.uuid(d) == Some(uuid))
                .and_then(|&d| devices.node(d));
        }
    }
    let nodes: Vec<String> = devices
        .list
        .iter()
        .filter_map(|&d| devices.node(d))
        .collect();
    (nodes.len() == 1)
        .then(|| nodes.into_iter().next())
        .flatten()
}

/// A surfaceless EGL context with GLES 3, current on the creating thread (tests and benches: no window, no compositor).
pub struct Headless {
    egl: egl::Instance<egl::Static>,
    display: egl::Display,
    context: egl::Context,
    pub gl: Rc<glow::Context>,
}

impl Headless {
    /// `gpu`: the render node to draw on (EGL device platform); None: Mesa's surfaceless platform.
    pub fn new(gpu: Option<&str>) -> Result<Self, String> {
        let egl = egl::Instance::new(egl::Static);
        let display = match gpu {
            Some(gpu) => {
                let devices = devices().ok_or("no EGL device enumeration")?;
                let device = devices
                    .list
                    .iter()
                    .copied()
                    .find(|&d| {
                        devices
                            .string(d, DRM_RENDER_NODE_FILE_EXT)
                            .and_then(|s| s.to_str().ok())
                            == Some(gpu)
                    })
                    .ok_or_else(|| format!("no EGL device with render node {gpu}"))?;
                // SAFETY: device is an EGLDeviceEXT from eglQueryDevicesEXT.
                unsafe {
                    egl.get_platform_display(PLATFORM_DEVICE_EXT, device, &[egl::ATTRIB_NONE])
                }
            }
            // SAFETY: EGL_DEFAULT_DISPLAY (null) is the native display of the surfaceless platform.
            None => unsafe {
                egl.get_platform_display(
                    PLATFORM_SURFACELESS_MESA,
                    std::ptr::null_mut(),
                    &[egl::ATTRIB_NONE],
                )
            },
        }
        .map_err(|e| format!("eglGetPlatformDisplay: {e}"))?;
        egl.initialize(display)
            .map_err(|e| format!("eglInitialize: {e}"))?;
        egl.bind_api(egl::OPENGL_ES_API)
            .map_err(|e| format!("eglBindAPI: {e}"))?;
        let attribs = [
            egl::SURFACE_TYPE,
            egl::PBUFFER_BIT,
            egl::RENDERABLE_TYPE,
            egl::OPENGL_ES3_BIT,
            egl::NONE,
        ];
        let config = egl
            .choose_first_config(display, &attribs)
            .ok()
            .flatten()
            .ok_or("no GLES 3 EGL config")?;
        let context = egl
            .create_context(
                display,
                config,
                None,
                &[egl::CONTEXT_MAJOR_VERSION, 3, egl::NONE],
            )
            .map_err(|e| format!("eglCreateContext: {e}"))?;
        egl.make_current(display, None, None, Some(context))
            .map_err(|e| format!("eglMakeCurrent: {e}"))?;
        // SAFETY: the context was just made current on this thread and outlives `gl` (Drop order below).
        let gl = Rc::new(unsafe { load_gl() });
        Ok(Self {
            egl,
            display,
            context,
            gl,
        })
    }
}

const LINUX_DMA_BUF_EXT: egl::Enum = 0x3270;
const LINUX_DRM_FOURCC_EXT: egl::Attrib = 0x3271;
const DMA_BUF_PLANE0_FD_EXT: egl::Attrib = 0x3272;
const DMA_BUF_PLANE0_OFFSET_EXT: egl::Attrib = 0x3273;
const DMA_BUF_PLANE0_PITCH_EXT: egl::Attrib = 0x3274;
const DMA_BUF_PLANE0_MODIFIER_LO_EXT: egl::Attrib = 0x3443;
const DMA_BUF_PLANE0_MODIFIER_HI_EXT: egl::Attrib = 0x3444;
const MOD_INVALID: u64 = 0x00ff_ffff_ffff_ffff;

type QueryDmaBufFormats = unsafe extern "system" fn(*mut c_void, i32, *mut i32, *mut i32) -> u32;
type QueryDmaBufModifiers =
    unsafe extern "system" fn(*mut c_void, i32, i32, *mut u64, *mut u32, *mut i32) -> u32;

/// One single-plane dmabuf as EGL takes it (EGL_EXT_image_dma_buf_import[_modifiers]).
#[derive(Clone, Copy, Debug)]
pub struct DmabufPlane {
    pub fd: i32,
    pub width: u32,
    pub height: u32,
    pub fourcc: u32,
    pub offset: u32,
    pub stride: u32,
    /// MOD_INVALID: no modifier attributes (the implicit layout).
    pub modifier: u64,
}

impl Headless {
    fn has_extension(&self, name: &str) -> bool {
        let list = self.egl.query_string(Some(self.display), egl::EXTENSIONS).ok();
        has_token(list, name)
    }

    /// Whether EGL imports `fourcc` with `modifier` for sampling (MOD_INVALID: the format at all).
    pub fn dmabuf_importable(&self, fourcc: u32, modifier: u64) -> bool {
        if !self.has_extension("EGL_EXT_image_dma_buf_import") {
            return false;
        }
        let dpy = self.display.as_ptr();
        if let Some(formats) = proc::<QueryDmaBufFormats>("eglQueryDmaBufFormatsEXT") {
            let mut n = 0;
            // SAFETY: a size query (max 0, null list) on our initialised display.
            if unsafe { formats(dpy, 0, std::ptr::null_mut(), &mut n) } == 0 || n <= 0 {
                return false;
            }
            let mut list = vec![0i32; n as usize];
            // SAFETY: list holds n entries.
            if unsafe { formats(dpy, n, list.as_mut_ptr(), &mut n) } == 0
                || !list[..n.max(0) as usize].contains(&(fourcc as i32))
            {
                return false;
            }
        }
        if modifier == MOD_INVALID {
            return true;
        }
        let Some(modifiers) = proc::<QueryDmaBufModifiers>("eglQueryDmaBufModifiersEXT")
            .filter(|_| self.has_extension("EGL_EXT_image_dma_buf_import_modifiers"))
        else {
            return modifier == 0; // linear needs no modifier support
        };
        let mut n = 0;
        // SAFETY: a size query on our display.
        if unsafe { modifiers(dpy, fourcc as i32, 0, std::ptr::null_mut(), std::ptr::null_mut(), &mut n) } == 0 || n <= 0 {
            return false;
        }
        let (mut mods, mut external) = (vec![0u64; n as usize], vec![0u32; n as usize]);
        // SAFETY: both lists hold n entries.
        if unsafe { modifiers(dpy, fourcc as i32, n, mods.as_mut_ptr(), external.as_mut_ptr(), &mut n) } == 0 {
            return false;
        }
        let n = n.max(0) as usize;
        mods[..n].iter().zip(&external[..n]).any(|(&m, &ext)| m == modifier && ext == 0)
    }

    /// An EGLImage over the dmabuf (EGL dups the descriptor; the caller keeps its own). None if EGL refuses it.
    pub fn import_dmabuf(&self, d: &DmabufPlane) -> Option<egl::Image> {
        let mut attribs: Vec<egl::Attrib> = vec![
            egl::WIDTH as egl::Attrib,
            d.width as egl::Attrib,
            egl::HEIGHT as egl::Attrib,
            d.height as egl::Attrib,
            LINUX_DRM_FOURCC_EXT,
            d.fourcc as egl::Attrib,
            DMA_BUF_PLANE0_FD_EXT,
            d.fd as egl::Attrib,
            DMA_BUF_PLANE0_OFFSET_EXT,
            d.offset as egl::Attrib,
            DMA_BUF_PLANE0_PITCH_EXT,
            d.stride as egl::Attrib,
        ];
        if d.modifier != MOD_INVALID {
            attribs.extend([
                DMA_BUF_PLANE0_MODIFIER_LO_EXT,
                (d.modifier & 0xffff_ffff) as egl::Attrib,
                DMA_BUF_PLANE0_MODIFIER_HI_EXT,
                (d.modifier >> 32) as egl::Attrib,
            ]);
        }
        attribs.push(egl::NONE as egl::Attrib);
        // SAFETY: EGL_NO_CONTEXT and a null client buffer are what EGL_LINUX_DMA_BUF_EXT requires.
        let (ctx, buffer) = unsafe {
            (
                egl::Context::from_ptr(egl::NO_CONTEXT),
                egl::ClientBuffer::from_ptr(std::ptr::null_mut()),
            )
        };
        self.egl
            .create_image(self.display, ctx, LINUX_DMA_BUF_EXT, buffer, &attribs)
            .ok()
    }

    pub fn destroy_image(&self, image: egl::Image) {
        let _ = self.egl.destroy_image(self.display, image);
    }
}

impl Drop for Headless {
    fn drop(&mut self) {
        let _ = self.egl.make_current(self.display, None, None, None);
        let _ = self.egl.destroy_context(self.display, self.context);
        let _ = self.egl.terminate(self.display);
    }
}
