//! Thin FFI over the libmpv client and render APIs (mpv/client.h, mpv/render.h, mpv/render_gl.h, client API 2.x):
//! only what the player uses. Layouts follow the C headers; values are the stable ABI constants.

#![allow(non_camel_case_types, dead_code)]

use std::ffi::{CStr, c_char, c_int, c_void};

#[repr(C)]
pub struct mpv_handle {
    _private: [u8; 0],
}

#[repr(C)]
pub struct mpv_render_context {
    _private: [u8; 0],
}

pub const FORMAT_NONE: c_int = 0;
pub const FORMAT_STRING: c_int = 1;
pub const FORMAT_FLAG: c_int = 3;
pub const FORMAT_INT64: c_int = 4;
pub const FORMAT_DOUBLE: c_int = 5;

pub const EVENT_NONE: c_int = 0;
pub const EVENT_LOG_MESSAGE: c_int = 2;
pub const EVENT_SET_PROPERTY_REPLY: c_int = 4;
pub const EVENT_COMMAND_REPLY: c_int = 5;
pub const EVENT_START_FILE: c_int = 6;
pub const EVENT_END_FILE: c_int = 7;
pub const EVENT_FILE_LOADED: c_int = 8;
pub const EVENT_IDLE: c_int = 11;
pub const EVENT_PROPERTY_CHANGE: c_int = 22;

pub const LOG_LEVEL_ERROR: c_int = 20;
pub const END_FILE_REASON_ERROR: c_int = 4;

pub const RENDER_PARAM_INVALID: c_int = 0;
pub const RENDER_PARAM_API_TYPE: c_int = 1;
pub const RENDER_PARAM_OPENGL_INIT_PARAMS: c_int = 2;
pub const RENDER_PARAM_OPENGL_FBO: c_int = 3;
pub const RENDER_PARAM_FLIP_Y: c_int = 4;
pub const RENDER_PARAM_WL_DISPLAY: c_int = 9;
pub const RENDER_PARAM_NEXT_FRAME_INFO: c_int = 11;
pub const RENDER_PARAM_BLOCK_FOR_TARGET_TIME: c_int = 12;
pub const RENDER_PARAM_DRM_DISPLAY_V2: c_int = 16;

pub const RENDER_API_TYPE_OPENGL: &CStr = c"opengl";
pub const RENDER_UPDATE_FRAME: u64 = 1;
pub const RENDER_FRAME_INFO_PRESENT: u64 = 1;
pub const RENDER_FRAME_INFO_REDRAW: u64 = 2;

#[repr(C)]
pub struct mpv_event {
    pub event_id: c_int,
    pub error: c_int,
    pub reply_userdata: u64,
    pub data: *mut c_void,
}

#[repr(C)]
pub struct mpv_event_property {
    pub name: *const c_char,
    pub format: c_int,
    pub data: *mut c_void,
}

#[repr(C)]
pub struct mpv_event_log_message {
    pub prefix: *const c_char,
    pub level: *const c_char,
    pub text: *const c_char,
    pub log_level: c_int,
}

#[repr(C)]
pub struct mpv_event_start_file {
    pub playlist_entry_id: i64,
}

#[repr(C)]
pub struct mpv_event_end_file {
    pub reason: c_int,
    pub error: c_int,
    pub playlist_entry_id: i64,
    pub playlist_insert_id: i64,
    pub playlist_insert_num_entries: c_int,
}

#[repr(C)]
pub struct mpv_render_param {
    pub kind: c_int,
    pub data: *mut c_void,
}

#[repr(C)]
#[derive(Default)]
pub struct mpv_render_frame_info {
    pub flags: u64,
    pub target_time: i64,
}

#[repr(C)]
pub struct mpv_opengl_init_params {
    pub get_proc_address: Option<unsafe extern "C" fn(*mut c_void, *const c_char) -> *mut c_void>,
    pub get_proc_address_ctx: *mut c_void,
}

#[repr(C)]
pub struct mpv_opengl_fbo {
    pub fbo: c_int,
    pub w: c_int,
    pub h: c_int,
    pub internal_format: c_int,
}

#[repr(C)]
pub struct mpv_opengl_drm_params_v2 {
    pub fd: c_int,
    pub crtc_id: c_int,
    pub connector_id: c_int,
    pub atomic_request_ptr: *mut *mut c_void,
    pub render_fd: c_int,
}

#[link(name = "mpv")]
unsafe extern "C" {
    pub fn mpv_create() -> *mut mpv_handle;
    pub fn mpv_initialize(ctx: *mut mpv_handle) -> c_int;
    pub fn mpv_terminate_destroy(ctx: *mut mpv_handle);
    pub fn mpv_error_string(error: c_int) -> *const c_char;
    pub fn mpv_set_option_string(
        ctx: *mut mpv_handle,
        name: *const c_char,
        data: *const c_char,
    ) -> c_int;
    pub fn mpv_request_log_messages(ctx: *mut mpv_handle, min_level: *const c_char) -> c_int;
    pub fn mpv_observe_property(
        ctx: *mut mpv_handle,
        userdata: u64,
        name: *const c_char,
        format: c_int,
    ) -> c_int;
    pub fn mpv_command_async(
        ctx: *mut mpv_handle,
        userdata: u64,
        args: *mut *const c_char,
    ) -> c_int;
    pub fn mpv_set_property_async(
        ctx: *mut mpv_handle,
        userdata: u64,
        name: *const c_char,
        format: c_int,
        data: *mut c_void,
    ) -> c_int;
    pub fn mpv_wait_event(ctx: *mut mpv_handle, timeout: f64) -> *mut mpv_event;
    pub fn mpv_get_time_ns(ctx: *mut mpv_handle) -> i64;

    pub fn mpv_render_context_create(
        res: *mut *mut mpv_render_context,
        mpv: *mut mpv_handle,
        params: *mut mpv_render_param,
    ) -> c_int;
    pub fn mpv_render_context_free(ctx: *mut mpv_render_context);
    pub fn mpv_render_context_render(
        ctx: *mut mpv_render_context,
        params: *mut mpv_render_param,
    ) -> c_int;
    pub fn mpv_render_context_update(ctx: *mut mpv_render_context) -> u64;
    pub fn mpv_render_context_report_swap(ctx: *mut mpv_render_context);
    pub fn mpv_render_context_get_info(
        ctx: *mut mpv_render_context,
        param: mpv_render_param,
    ) -> c_int;
    pub fn mpv_render_context_set_update_callback(
        ctx: *mut mpv_render_context,
        callback: Option<unsafe extern "C" fn(*mut c_void)>,
        callback_ctx: *mut c_void,
    );
}

/// mpv's text for an error code (a static string).
pub fn error_string(error: c_int) -> &'static str {
    // SAFETY: mpv_error_string returns a static NUL-terminated string for any code.
    unsafe { CStr::from_ptr(mpv_error_string(error)) }
        .to_str()
        .unwrap_or("mpv error")
}

/// Bytes of a C string, None for null.
///
/// # Safety
/// `p` is null or a NUL-terminated string valid for 'a.
pub unsafe fn c_str<'a>(p: *const c_char) -> Option<&'a CStr> {
    // SAFETY: the caller's contract.
    (!p.is_null()).then(|| unsafe { CStr::from_ptr(p) })
}
