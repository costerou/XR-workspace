//! The usbdevfs ABI and the one place that calls the kernel.
//!
//! Only five requests can be expressed at all: claim, release, submit, reap without waiting, and
//! discard. `Request` has no variant for a reset, a halt clear, a configuration or alternate setting,
//! a disconnect or a control transfer, so none of those can be sent by accident. `LibcSys` checks the
//! numeric code against `is_allowed_ioctl` as well, the same second lock the C++ `luma::Sys` has.
//!
//! The struct layout and request codes are those of `<linux/usbdevice_fs.h>` on 64-bit Linux. The
//! table in `REQUESTS` is checked line by line against `rust/corpus/luma/transport_trace.txt`, which
//! the C++ test writes from the kernel header itself.

use std::ffi::{CString, c_int, c_void};
use std::os::fd::RawFd;
use std::os::unix::ffi::OsStrExt;
use std::path::Path;
use std::ptr::NonNull;

#[cfg(not(all(target_os = "linux", any(target_arch = "x86_64", target_arch = "aarch64"))))]
compile_error!("the usbdevfs layout and request codes here are those of 64-bit Linux on x86_64 or aarch64");

pub const URB_TYPE_ISO: u8 = 0;
pub const URB_TYPE_INTERRUPT: u8 = 1;
pub const URB_TYPE_CONTROL: u8 = 2;
pub const URB_TYPE_BULK: u8 = 3;

/// `struct usbdevfs_urb` without its trailing `iso_frame_desc[]`, which bulk and interrupt URBs do not use.
#[repr(C)]
#[derive(Debug)]
pub struct Urb {
    pub kind: u8,
    pub endpoint: u8,
    pub status: i32,
    pub flags: u32,
    pub buffer: *mut c_void,
    pub buffer_length: i32,
    pub actual_length: i32,
    pub start_frame: i32,
    /// A union with `stream_id` in the kernel. Both are 0 here.
    pub number_of_packets: i32,
    pub error_count: i32,
    pub signr: u32,
    pub usercontext: *mut c_void,
}

impl Default for Urb {
    fn default() -> Self {
        Urb {
            kind: 0,
            endpoint: 0,
            status: 0,
            flags: 0,
            buffer: std::ptr::null_mut(),
            buffer_length: 0,
            actual_length: 0,
            start_frame: 0,
            number_of_packets: 0,
            error_count: 0,
            signr: 0,
            usercontext: std::ptr::null_mut(),
        }
    }
}

pub const CLAIMINTERFACE: u64 = 0x8004_550f;
pub const RELEASEINTERFACE: u64 = 0x8004_5510;
pub const SUBMITURB: u64 = 0x8038_550a;
pub const REAPURBNDELAY: u64 = 0x4008_550d;
pub const DISCARDURB: u64 = 0x550b;

/// Every request `<linux/usbdevice_fs.h>` defines, by name, in the header's order.
pub const REQUESTS: [(&str, u64); 31] = [
    ("CONTROL", 0xc018_5500),
    ("BULK", 0xc018_5502),
    ("RESETEP", 0x8004_5503),
    ("SETINTERFACE", 0x8008_5504),
    ("SETCONFIGURATION", 0x8004_5505),
    ("GETDRIVER", 0x4104_5508),
    ("SUBMITURB", SUBMITURB),
    ("DISCARDURB", DISCARDURB),
    ("REAPURB", 0x4008_550c),
    ("REAPURBNDELAY", REAPURBNDELAY),
    ("DISCSIGNAL", 0x8010_550e),
    ("CLAIMINTERFACE", CLAIMINTERFACE),
    ("RELEASEINTERFACE", RELEASEINTERFACE),
    ("CONNECTINFO", 0x4008_5511),
    ("IOCTL", 0xc010_5512),
    ("HUB_PORTINFO", 0x8080_5513),
    ("RESET", 0x5514),
    ("CLEAR_HALT", 0x8004_5515),
    ("DISCONNECT", 0x5516),
    ("CONNECT", 0x5517),
    ("CLAIM_PORT", 0x8004_5518),
    ("RELEASE_PORT", 0x8004_5519),
    ("GET_CAPABILITIES", 0x8004_551a),
    ("DISCONNECT_CLAIM", 0x8108_551b),
    ("ALLOC_STREAMS", 0x8008_551c),
    ("FREE_STREAMS", 0x8008_551d),
    ("DROP_PRIVILEGES", 0x4004_551e),
    ("GET_SPEED", 0x551f),
    ("FORBID_SUSPEND", 0x5521),
    ("ALLOW_SUSPEND", 0x5522),
    ("WAIT_FOR_RESUME", 0x5523),
];

/// The only ioctls the transport may issue: claim, release, submit, reap and discard.
pub fn is_allowed_ioctl(code: u64) -> bool {
    matches!(code, CLAIMINTERFACE | RELEASEINTERFACE | SUBMITURB | REAPURBNDELAY | DISCARDURB)
}

/// One usbdevfs request with its argument.
#[derive(Debug)]
pub enum Request<'a> {
    ClaimInterface(u32),
    ReleaseInterface(u32),
    SubmitUrb(NonNull<Urb>),
    DiscardUrb(NonNull<Urb>),
    /// The kernel stores the address of a completed URB here.
    ReapUrbNdelay(&'a mut *mut Urb),
}

impl Request<'_> {
    pub fn code(&self) -> u64 {
        match self {
            Request::ClaimInterface(_) => CLAIMINTERFACE,
            Request::ReleaseInterface(_) => RELEASEINTERFACE,
            Request::SubmitUrb(_) => SUBMITURB,
            Request::DiscardUrb(_) => DISCARDURB,
            Request::ReapUrbNdelay(_) => REAPURBNDELAY,
        }
    }
}

/// An errno value. `message()` is the C library's text for it, as `strerror` gives it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Errno(pub i32);

impl Errno {
    pub fn last() -> Errno {
        Errno(std::io::Error::last_os_error().raw_os_error().unwrap_or(libc::EIO))
    }

    pub fn message(self) -> String {
        let text = std::io::Error::from_raw_os_error(self.0).to_string();
        match text.rfind(" (os error ") {
            Some(cut) => text[..cut].to_string(),
            None => text,
        }
    }
}

/// The system calls the transport makes on the device, so tests can stand in for the kernel.
pub trait Sys: Send + Sync {
    fn open(&self, path: &Path, flags: c_int) -> Result<RawFd, Errno>;
    fn close(&self, fd: RawFd);
    /// Returns how many entries have events.
    fn poll(&self, fds: &mut [libc::pollfd], timeout_ms: c_int) -> Result<usize, Errno>;
    /// # Safety
    /// A submitted URB, and the buffer it points at, must stay valid and unmoved until it is reaped or
    /// the file is closed: the kernel writes status, length and data there when it is reaped.
    unsafe fn ioctl(&self, fd: RawFd, request: Request<'_>) -> Result<(), Errno>;
}

/// The real kernel.
#[derive(Debug, Default)]
pub struct LibcSys;

impl Sys for LibcSys {
    fn open(&self, path: &Path, flags: c_int) -> Result<RawFd, Errno> {
        let path = CString::new(path.as_os_str().as_bytes()).map_err(|_| Errno(libc::EINVAL))?;
        // SAFETY: a valid NUL-terminated path; open has no other preconditions.
        let fd = unsafe { libc::open(path.as_ptr(), flags) };
        if fd < 0 { Err(Errno::last()) } else { Ok(fd) }
    }

    fn close(&self, fd: RawFd) {
        // SAFETY: the transport owns fd and closes it once.
        unsafe { libc::close(fd) };
    }

    fn poll(&self, fds: &mut [libc::pollfd], timeout_ms: c_int) -> Result<usize, Errno> {
        // SAFETY: fds is a valid slice of pollfd for its whole length.
        let ready = unsafe { libc::poll(fds.as_mut_ptr(), fds.len() as libc::nfds_t, timeout_ms) };
        if ready < 0 { Err(Errno::last()) } else { Ok(ready as usize) }
    }

    unsafe fn ioctl(&self, fd: RawFd, request: Request<'_>) -> Result<(), Errno> {
        let code = request.code();
        if !is_allowed_ioctl(code) {
            return Err(Errno(libc::EPERM)); // Second lock: holds even if Request ever grows a variant.
        }
        let mut number = match request {
            Request::ClaimInterface(n) | Request::ReleaseInterface(n) => n,
            _ => 0,
        };
        let argument: *mut c_void = match request {
            Request::ClaimInterface(_) | Request::ReleaseInterface(_) => (&raw mut number).cast(),
            Request::SubmitUrb(urb) | Request::DiscardUrb(urb) => urb.as_ptr().cast(),
            Request::ReapUrbNdelay(slot) => (slot as *mut *mut Urb).cast(),
        };
        // SAFETY: argument points at what the request expects; the caller keeps URBs alive (see the trait).
        let result = unsafe { libc::ioctl(fd, code as _, argument) };
        if result < 0 { Err(Errno::last()) } else { Ok(()) }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::mem::{offset_of, size_of};

    #[test]
    fn urb_layout_is_the_kernels() {
        assert_eq!(size_of::<Urb>(), 56);
        assert_eq!(offset_of!(Urb, kind), 0);
        assert_eq!(offset_of!(Urb, endpoint), 1);
        assert_eq!(offset_of!(Urb, status), 4);
        assert_eq!(offset_of!(Urb, flags), 8);
        assert_eq!(offset_of!(Urb, buffer), 16);
        assert_eq!(offset_of!(Urb, buffer_length), 24);
        assert_eq!(offset_of!(Urb, actual_length), 28);
        assert_eq!(offset_of!(Urb, start_frame), 32);
        assert_eq!(offset_of!(Urb, number_of_packets), 36);
        assert_eq!(offset_of!(Urb, error_count), 40);
        assert_eq!(offset_of!(Urb, signr), 44);
        assert_eq!(offset_of!(Urb, usercontext), 48);
        // The size is encoded in the request code too.
        assert_eq!((SUBMITURB >> 16) & 0x3fff, size_of::<Urb>() as u64);
    }

    #[test]
    fn only_the_harmless_ioctls_are_allowed() {
        let allowed: Vec<&str> = REQUESTS.iter().filter(|(_, code)| is_allowed_ioctl(*code)).map(|(name, _)| *name).collect();
        assert_eq!(allowed, ["SUBMITURB", "DISCARDURB", "REAPURBNDELAY", "CLAIMINTERFACE", "RELEASEINTERFACE"]);
        for name in ["RESET", "CLEAR_HALT", "SETINTERFACE", "SETCONFIGURATION", "DISCONNECT", "RESETEP", "IOCTL", "CONTROL", "BULK", "DISCONNECT_CLAIM"] {
            let code = REQUESTS.iter().find(|(n, _)| *n == name).unwrap().1;
            assert!(!is_allowed_ioctl(code), "{name}");
        }
    }

    #[test]
    fn libc_sys_refuses_a_forbidden_code_before_the_kernel() {
        // Request cannot hold RESET, so check the lock on the code path it guards: a real file, a real call.
        let sys = LibcSys;
        let fd = sys.open(Path::new("/dev/null"), libc::O_RDWR | libc::O_CLOEXEC).unwrap();
        let mut done: *mut Urb = std::ptr::null_mut();
        // SAFETY: REAPURBNDELAY on /dev/null writes nothing: the kernel answers ENOTTY.
        assert_eq!(unsafe { sys.ioctl(fd, Request::ReapUrbNdelay(&mut done)) }, Err(Errno(libc::ENOTTY)));
        sys.close(fd);
    }

    #[test]
    fn errno_text_is_strerror() {
        assert_eq!(Errno(libc::ENODEV).message(), "No such device");
        assert_eq!(Errno(libc::EBUSY).message(), "Device or resource busy");
    }
}
