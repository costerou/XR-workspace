//! VR video (docs/vr-video.md): libmpv decode into a GL texture, then the 360/180/fisheye projection pass.

pub mod card;
pub mod math;
pub mod mode;
pub mod mpv;
pub mod player;
pub mod readback;
pub mod sphere;
