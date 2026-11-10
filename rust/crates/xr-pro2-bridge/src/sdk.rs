//! The SDK, loaded at run time from the pinned descriptor, never linked.
//!
//! Only the functions the bridge calls are declared, with the argument types it passes, written
//! here from how `tools/pro2_runtime/bridge.cpp` calls them (docs/pro2/sdk-licence/README.md:
//! no VITURE header is copied or included). Every one is resolved before `create`, so a library
//! that lacks a cleanup function cannot strand a handle.
//!
//! The library is never unloaded: after a failed stop the SDK may still call back, and the
//! callback must stay mapped until the process exits.

use libloading::os::unix::{Library, RTLD_LOCAL, RTLD_NOW};
use std::ffi::{c_char, c_int, c_void};

pub type PoseCallback = extern "C" fn(*mut f32, u64);
type HandleCall = unsafe extern "C" fn(*mut c_void) -> c_int;

const PREFIX: &str = "xr_device_provider_";

pub struct Sdk {
    pub create: unsafe extern "C" fn(c_int) -> *mut c_void,
    pub destroy: unsafe extern "C" fn(*mut c_void),
    pub start: HandleCall,
    pub stop: HandleCall,
    pub shutdown: HandleCall,
    pub get_device_type: HandleCall,
    pub initialize: unsafe extern "C" fn(*mut c_void, *const c_char, *const c_char) -> c_int,
    pub register_imu_pose_callback: unsafe extern "C" fn(*mut c_void, PoseCallback) -> c_int,
    pub open_imu: unsafe extern "C" fn(*mut c_void, u8, u8) -> c_int,
    pub close_imu: unsafe extern "C" fn(*mut c_void, u8) -> c_int,
    pub get_glasses_version: unsafe extern "C" fn(*mut c_void, *mut c_char, *mut c_int) -> c_int,
    pub is_product_id_valid: unsafe extern "C" fn(c_int) -> c_int,
    pub is_product_support_imu_frequency: unsafe extern "C" fn(c_int, c_int, c_int) -> c_int,
    /// Only resolved when the run takes display requests.
    pub set_display_mode: Option<unsafe extern "C" fn(*mut c_void, c_int) -> c_int>,
}

/// dlopen of `/proc/self/fd/<fd>`, RTLD_NOW | RTLD_LOCAL, as the C++ bridge. The caller has
/// checked the file's SHA-256 through the same descriptor.
pub fn load(fd: i32) -> Result<&'static Library, &'static str> {
    // SAFETY: running the library's initialisers is what loading it means; its hash was pinned.
    let library =
        unsafe { Library::open(Some(format!("/proc/self/fd/{fd}")), RTLD_NOW | RTLD_LOCAL) };
    // Leaked on purpose: never dlclose (see the module comment).
    library
        .map(|l| &*Box::leak(Box::new(l)))
        .map_err(|_| "sdk-load")
}

/// One exported function by its short name.
///
/// # Safety
/// `T` must be the function's real type.
unsafe fn get<T: Copy>(library: &'static Library, name: &str) -> Result<T, &'static str> {
    let full = format!("{PREFIX}{name}\0");
    // SAFETY: the caller vouches for T; the library lives for the process.
    unsafe { library.get::<T>(full.as_bytes()) }
        .map(|s| *s)
        .map_err(|_| "sdk-symbol")
}

impl Sdk {
    pub fn resolve(library: &'static Library, display: bool) -> Result<Self, &'static str> {
        // SAFETY: each type is the call as the C++ bridge makes it.
        unsafe {
            Ok(Sdk {
                create: get(library, "create")?,
                destroy: get(library, "destroy")?,
                start: get(library, "start")?,
                stop: get(library, "stop")?,
                shutdown: get(library, "shutdown")?,
                get_device_type: get(library, "get_device_type")?,
                initialize: get(library, "initialize")?,
                register_imu_pose_callback: get(library, "register_imu_pose_callback")?,
                open_imu: get(library, "open_imu")?,
                close_imu: get(library, "close_imu")?,
                get_glasses_version: get(library, "get_glasses_version")?,
                set_display_mode: if display {
                    Some(get(library, "set_display_mode")?)
                } else {
                    None
                },
                is_product_id_valid: get(library, "is_product_id_valid")?,
                is_product_support_imu_frequency: get(library, "is_product_support_imu_frequency")?,
            })
        }
    }
}
