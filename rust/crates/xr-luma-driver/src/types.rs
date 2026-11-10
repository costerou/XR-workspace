//! Shared value types of the Luma driver. Port of `spike/driver/include/luma/types.hpp`.

/// One inertial sample in SI units, body frame: x forward, y left, z up, and accel as specific
/// force (+9.8 on z when level). The protocol layer remaps the Luma Ultra's wire axes into this.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct ImuSample {
    pub t_ns: u64,
    pub gyro_rad_s: [f32; 3],
    pub accel_m_s2: [f32; 3],
    pub mag: Option<[f32; 3]>,
    pub seq: u32,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Quatf {
    pub w: f32,
    pub x: f32,
    pub y: f32,
    pub z: f32,
}

impl Default for Quatf {
    fn default() -> Self {
        Self { w: 1.0, x: 0.0, y: 0.0, z: 0.0 }
    }
}

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct PoseOut {
    pub q: Quatf,
    pub t_ns: u64,
    pub seq: u32,
    pub calibrated: bool,
}
