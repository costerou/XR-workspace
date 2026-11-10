//! A stand-in for the driver's fusion with the same update() shape: a plain Mahony filter, so the
//! benchmark carries about the arithmetic the real thing will. The port of
//! `spike/driver/bench/stub_fusion.hpp`; the bench switches to the real fusion once it is ported.

#[derive(Debug, Clone, Copy)]
pub struct Quat {
    pub w: f32,
    pub x: f32,
    pub y: f32,
    pub z: f32,
}

impl Default for Quat {
    fn default() -> Self {
        Self {
            w: 1.0,
            x: 0.0,
            y: 0.0,
            z: 0.0,
        }
    }
}

#[derive(Debug, Clone, Copy, Default)]
pub struct ImuSample {
    pub t_ns: u64,
    pub seq: u32,
    pub gyro_rad_s: [f32; 3],
    pub accel_m_s2: [f32; 3],
}

#[derive(Debug, Clone, Copy)]
pub struct PoseOut {
    pub q: Quat,
    pub seq: u32,
    pub calibrated: bool,
}

#[derive(Default)]
pub struct StubFusion {
    pose: Quat,
    last_ns: u64,
    ix: f32,
    iy: f32,
    iz: f32,
}

const KP: f32 = 2.0;
const KI: f32 = 0.01;

impl StubFusion {
    pub fn update(&mut self, s: &ImuSample) -> PoseOut {
        let dt = if self.last_ns != 0 {
            s.t_ns.wrapping_sub(self.last_ns) as f32 * 1e-9
        } else {
            1e-3
        };
        self.last_ns = s.t_ns;
        let [mut gx, mut gy, mut gz] = s.gyro_rad_s;
        let [mut ax, mut ay, mut az] = s.accel_m_s2;
        let q = &mut self.pose;
        let n = (ax * ax + ay * ay + az * az).sqrt();
        if n > 1e-3 {
            ax /= n;
            ay /= n;
            az /= n;
            let vx = 2.0 * (q.x * q.z - q.w * q.y);
            let vy = 2.0 * (q.w * q.x + q.y * q.z);
            let vz = q.w * q.w - q.x * q.x - q.y * q.y + q.z * q.z;
            let ex = ay * vz - az * vy;
            let ey = az * vx - ax * vz;
            let ez = ax * vy - ay * vx;
            self.ix += KI * ex * dt;
            self.iy += KI * ey * dt;
            self.iz += KI * ez * dt;
            gx += KP * ex + self.ix;
            gy += KP * ey + self.iy;
            gz += KP * ez + self.iz;
        }
        let (hx, hy, hz) = (gx * dt / 2.0, gy * dt / 2.0, gz * dt / 2.0);
        let p = *q;
        q.w += -p.x * hx - p.y * hy - p.z * hz;
        q.x += p.w * hx + p.y * hz - p.z * hy;
        q.y += p.w * hy - p.x * hz + p.z * hx;
        q.z += p.w * hz + p.x * hy - p.y * hx;
        let len = (q.w * q.w + q.x * q.x + q.y * q.y + q.z * q.z).sqrt();
        q.w /= len;
        q.x /= len;
        q.y /= len;
        q.z /= len;
        PoseOut {
            q: *q,
            seq: s.seq,
            calibrated: true,
        }
    }
}
