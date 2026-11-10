//! GBM dmabuf allocation for zero-copy capture and the GL swapchain (spike/src/dmabuf.cpp).
//!
//! The device is the render node of a `dev_t` the compositor advertised (a capture session's `dmabuf_device`, or the
//! linux-dmabuf main device), found through sysfs. Every failure is one of the fixed reasons the C++ logs, verbatim:
//! the capture falls back to SHM with that reason in its exit report.

use std::fs::{File, OpenOptions};
use std::os::fd::OwnedFd;
use std::os::unix::fs::OpenOptionsExt;

use drm_fourcc::{DrmFourcc, DrmModifier};
use gbm::{BufferObject, BufferObjectFlags, Device};

use crate::capture::MOD_INVALID;

/// The render node (`/dev/dri/renderD*`) of the DRM device `dev` (a card or a render node), from
/// `/sys/dev/char/MAJ:MIN/device/drm`. None when sysfs names none.
pub fn render_node(dev: u64) -> Option<String> {
    let (major, minor) = (libc::major(dev as libc::dev_t), libc::minor(dev as libc::dev_t));
    let dir = std::fs::read_dir(format!("/sys/dev/char/{major}:{minor}/device/drm")).ok()?;
    let mut nodes: Vec<String> = dir
        .filter_map(|e| e.ok()?.file_name().into_string().ok())
        .filter(|n| n.starts_with("renderD") && n[7..].bytes().all(|c| c.is_ascii_digit()) && n.len() > 7)
        .collect();
    nodes.sort();
    nodes.first().map(|n| format!("/dev/dri/{n}"))
}

/// A `dev_t` as the protocols send it: a native-endian array of exactly its size.
pub fn dev_from_array(bytes: &[u8]) -> Option<u64> {
    match bytes.len() {
        8 => Some(u64::from_ne_bytes(bytes.try_into().ok()?)),
        4 => Some(u64::from(u32::from_ne_bytes(bytes.try_into().ok()?))),
        _ => None,
    }
}

/// A GBM device on one render node.
pub struct Allocator {
    device: Device<File>,
    dev: u64,
    node: String,
}

/// One single-plane buffer object and its exported descriptor.
pub struct Dmabuf {
    pub fd: OwnedFd,
    pub width: u32,
    pub height: u32,
    pub stride: u32,
    pub offset: u32,
    pub fourcc: u32,
    /// The buffer's modifier; [`MOD_INVALID`] when it was allocated without one (implicit layout).
    pub modifier: u64,
    _bo: BufferObject<()>,
}

impl Allocator {
    pub fn open(dev: u64) -> Result<Self, &'static str> {
        let node = render_node(dev).ok_or("no render node for advertised dmabuf device")?;
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .custom_flags(libc::O_CLOEXEC)
            .open(&node)
            .map_err(|_| "render node open failed")?;
        let device = Device::new(file).map_err(|_| "gbm device failed")?;
        Ok(Self { device, dev, node })
    }

    /// The `dev_t` this allocator was opened for.
    pub fn dev(&self) -> u64 {
        self.dev
    }

    pub fn node(&self) -> &str {
        &self.node
    }

    /// A `width` x `height` buffer in `fourcc` with exactly `modifier` ([`MOD_INVALID`]: the driver's implicit
    /// layout), usable for rendering (and scanout when `scanout`).
    pub fn allocate(
        &self,
        width: u32,
        height: u32,
        fourcc: u32,
        modifier: u64,
        scanout: bool,
    ) -> Result<Dmabuf, &'static str> {
        let format = DrmFourcc::try_from(fourcc).map_err(|_| "gbm allocation failed")?;
        let mut usage = BufferObjectFlags::RENDERING;
        if scanout {
            usage |= BufferObjectFlags::SCANOUT;
        }
        let bo: BufferObject<()> = if modifier == MOD_INVALID {
            self.device.create_buffer_object(width, height, format, usage)
        } else {
            self.device.create_buffer_object_with_modifiers2(
                width,
                height,
                format,
                std::iter::once(DrmModifier::from(modifier)),
                usage,
            )
        }
        .map_err(|_| "gbm allocation failed")?;
        if bo.plane_count() != 1 {
            return Err("unexpected plane count");
        }
        let got = u64::from(bo.modifier());
        if modifier != MOD_INVALID && got != modifier {
            return Err("gbm returned a different modifier");
        }
        let fd = bo.fd_for_plane(0).map_err(|_| "dmabuf export failed")?;
        Ok(Dmabuf {
            fd,
            width,
            height,
            stride: bo.stride_for_plane(0),
            offset: bo.offset(0),
            fourcc,
            modifier: if modifier == MOD_INVALID { MOD_INVALID } else { got },
            _bo: bo,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dev_arrays() {
        assert_eq!(dev_from_array(&0xe280u64.to_ne_bytes()), Some(0xe280));
        assert_eq!(dev_from_array(&[1, 2, 3]), None);
    }

    #[test]
    fn unknown_device_has_no_node() {
        // major 1 is mem (/dev/null etc.): never a DRM device.
        assert_eq!(render_node(libc::makedev(1, 3) as u64), None);
        assert_eq!(Allocator::open(libc::makedev(1, 3) as u64).err(), Some("no render node for advertised dmabuf device"));
    }
}
