//! Orientation maths the view, HUD and dumps need (from `spike/src/pose.cpp`).
//!
//! Only the pure functions: the shared-memory feed, `PoseState` and `ModeController` stay in C++ for now.
//! All maths is `f32`, in the same order as the C++, so dumps match it bit for bit.

use std::f32::consts::PI;

/// A unit quaternion in the driver frame (x forward, y left, z up).
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Quat {
    pub x: f32,
    pub y: f32,
    pub z: f32,
    pub w: f32,
}

impl Default for Quat {
    fn default() -> Self {
        Self { x: 0.0, y: 0.0, z: 0.0, w: 1.0 }
    }
}

/// Row-major 3x3 matrix.
pub type Mat3 = [f32; 9];

pub const IDENTITY: Mat3 = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0];

/// gl = M * driver: right = -left, up = up, back = -forward. A proper rotation (det +1).
const DRIVER_TO_GL: Mat3 = [0.0, -1.0, 0.0, 0.0, 0.0, 1.0, -1.0, 0.0, 0.0];
/// Radians: this near half a turn, which way round is the push's to say.
const HALF_TURN_SLACK: f32 = 1e-2;

pub fn quat_mul(a: Quat, b: Quat) -> Quat {
    Quat {
        x: a.w * b.x + a.x * b.w + a.y * b.z - a.z * b.y,
        y: a.w * b.y - a.x * b.z + a.y * b.w + a.z * b.x,
        z: a.w * b.z + a.x * b.y - a.y * b.x + a.z * b.w,
        w: a.w * b.w - a.x * b.x - a.y * b.y - a.z * b.z,
    }
}

pub fn quat_conj(q: Quat) -> Quat {
    Quat { x: -q.x, y: -q.y, z: -q.z, w: q.w }
}

/// `None` if non-finite or near zero length.
pub fn quat_normalized(q: Quat) -> Option<Quat> {
    let n = (q.x * q.x + q.y * q.y + q.z * q.z + q.w * q.w).sqrt();
    if !n.is_finite() || n < 1e-6 {
        return None;
    }
    Some(Quat { x: q.x / n, y: q.y / n, z: q.z / n, w: q.w / n })
}

pub fn quat_to_mat3(q: Quat) -> Mat3 {
    let (x, y, z, w) = (q.x, q.y, q.z, q.w);
    [
        1.0 - 2.0 * (y * y + z * z),
        2.0 * (x * y - w * z),
        2.0 * (x * z + w * y),
        2.0 * (x * y + w * z),
        1.0 - 2.0 * (x * x + z * z),
        2.0 * (y * z - w * x),
        2.0 * (x * z - w * y),
        2.0 * (y * z + w * x),
        1.0 - 2.0 * (x * x + y * y),
    ]
}

/// `quat_to_mat3`'s inverse for a rotation matrix (identity if degenerate).
pub fn mat3_to_quat(m: &Mat3) -> Quat {
    // Shepperd: from the largest of w, x, y, z, so no square root is taken of a small difference.
    let tr = m[0] + m[4] + m[8];
    let q = if tr > 0.0 {
        let s = (tr + 1.0).sqrt() * 2.0;
        Quat { x: (m[7] - m[5]) / s, y: (m[2] - m[6]) / s, z: (m[3] - m[1]) / s, w: s / 4.0 }
    } else if m[0] > m[4] && m[0] > m[8] {
        let s = (1.0 + m[0] - m[4] - m[8]).sqrt() * 2.0;
        Quat { x: s / 4.0, y: (m[1] + m[3]) / s, z: (m[2] + m[6]) / s, w: (m[7] - m[5]) / s }
    } else if m[4] > m[8] {
        let s = (1.0 + m[4] - m[0] - m[8]).sqrt() * 2.0;
        Quat { x: (m[1] + m[3]) / s, y: s / 4.0, z: (m[5] + m[7]) / s, w: (m[2] - m[6]) / s }
    } else {
        let s = (1.0 + m[8] - m[0] - m[4]).sqrt() * 2.0;
        Quat { x: (m[2] + m[6]) / s, y: (m[5] + m[7]) / s, z: s / 4.0, w: (m[3] - m[1]) / s }
    };
    quat_normalized(q).unwrap_or_default()
}

pub fn mul(a: &Mat3, b: &Mat3) -> Mat3 {
    let mut r = [0.0f32; 9];
    for i in 0..3 {
        for j in 0..3 {
            for k in 0..3 {
                r[i * 3 + j] += a[i * 3 + k] * b[k * 3 + j];
            }
        }
    }
    r
}

pub fn transpose(m: &Mat3) -> Mat3 {
    [m[0], m[3], m[6], m[1], m[4], m[7], m[2], m[5], m[8]]
}

pub fn driver_to_gl(driver: &Mat3) -> Mat3 {
    mul(&mul(&DRIVER_TO_GL, driver), &transpose(&DRIVER_TO_GL))
}

pub fn apply(m: &Mat3, v: [f32; 3]) -> [f32; 3] {
    [
        m[0] * v[0] + m[1] * v[1] + m[2] * v[2],
        m[3] * v[0] + m[4] * v[1] + m[5] * v[2],
        m[6] * v[0] + m[7] * v[1] + m[8] * v[2],
    ]
}

pub fn project(view: &Mat3, ndc_x: f32, ndc_y: f32, hw: f32, hh: f32) -> Option<[f32; 2]> {
    let v = apply(view, [ndc_x * hw, ndc_y * hh, -1.0]);
    if v[2] > -1e-4 || hw <= 0.0 || hh <= 0.0 {
        return None;
    }
    Some([v[0] / -v[2] / hw, v[1] / -v[2] / hh])
}

/// Shortest arc.
pub fn quat_slerp(a: Quat, mut b: Quat, t: f32) -> Quat {
    let mut d = a.x * b.x + a.y * b.y + a.z * b.z + a.w * b.w;
    if d < 0.0 {
        b = Quat { x: -b.x, y: -b.y, z: -b.z, w: -b.w };
        d = -d;
    }
    let (mut wa, mut wb) = (1.0 - t, t);
    if d < 0.9995 {
        let th = d.min(1.0).acos();
        let s = th.sin();
        wa = ((1.0 - t) * th).sin() / s;
        wb = (t * th).sin() / s;
    }
    quat_normalized(Quat {
        x: wa * a.x + wb * b.x,
        y: wa * a.y + wb * b.y,
        z: wa * a.z + wb * b.z,
        w: wa * a.w + wb * b.w,
    })
    .unwrap_or(b)
}

/// Radians between orientations (atan2 form: accurate near zero, where acos is not).
pub fn quat_angle(a: Quat, b: Quat) -> f32 {
    let d = quat_mul(quat_conj(a), b);
    2.0 * (d.x * d.x + d.y * d.y + d.z * d.z).sqrt().atan2(d.w.abs())
}

/// Same yaw and pitch of the forward axis, zero roll (driver frame).
pub fn level(q: Quat) -> Quat {
    let f = apply(&quat_to_mat3(q), [1.0, 0.0, 0.0]); // forward (x) in the reference frame; z up
    let yaw = f[1].atan2(f[0]);
    let pitch = f[2].clamp(-1.0, 1.0).asin();
    let qy = Quat { x: 0.0, y: 0.0, z: (yaw / 2.0).sin(), w: (yaw / 2.0).cos() };
    let qp = Quat { x: 0.0, y: (-pitch / 2.0).sin(), z: 0.0, w: (-pitch / 2.0).cos() }; // +pitch tips forward toward +z
    quat_mul(qy, qp)
}

/// Render rotation for a canvas anchored at `anchor`.
pub fn view_from(head: Quat, anchor: Quat) -> Mat3 {
    transpose(&driver_to_gl(&quat_to_mat3(quat_mul(quat_conj(anchor), head))))
}

pub fn canvas_up(reference: Quat, anchor: Quat) -> [f32; 3] {
    let v = apply(&transpose(&quat_to_mat3(quat_mul(reference, anchor))), [0.0, 0.0, 1.0]);
    apply(&DRIVER_TO_GL, v)
}

pub fn aim_rotation(az: f32, el: f32) -> Quat {
    // GL: the view must apply R^T = El(el)^T . Az(az)^T (place_point's R = Az(az) . El(el)); in the driver frame
    // (x forward, y left, z up) that is a pitch of el about +y after a yaw of az about +z.
    quat_mul(
        Quat { x: 0.0, y: (el / 2.0).sin(), z: 0.0, w: (el / 2.0).cos() },
        Quat { x: 0.0, y: 0.0, z: (az / 2.0).sin(), w: (az / 2.0).cos() },
    )
}

pub fn aim_along(from: Quat, to: Quat, way: f32, turned: f32, side: u8) -> Quat {
    let s = if way > 0.0 { (turned / way).clamp(0.0, 1.0) } else { 1.0 };
    if way < PI - HALF_TURN_SLACK {
        return quat_slerp(from, to, s);
    }
    let mut d = quat_mul(quat_conj(from), to);
    if d.w < 0.0 {
        d = Quat { x: -d.x, y: -d.y, z: -d.z, w: -d.w };
    }
    let sn = (d.x * d.x + d.y * d.y + d.z * d.z).sqrt();
    #[allow(clippy::neg_cmp_op_on_partial_ord)] // true for NaN too, as in the C++
    if !(sn > 1e-6) {
        return if s < 1.0 { from } else { to };
    }
    // The push as a turn of the canvas: right about -y, left about +y, up about +x, down about -x
    // (GL frame, one quarter of `from`'s view).
    const PUSH: [[f32; 3]; 4] = [[0.0, -1.0, 0.0], [0.0, 1.0, 0.0], [1.0, 0.0, 0.0], [-1.0, 0.0, 0.0]];
    let push = apply(&transpose(&DRIVER_TO_GL), PUSH[usize::from(side.min(3))]);
    let mut axis = [d.x / sn, d.y / sn, d.z / sn];
    let mut angle = 2.0 * sn.atan2(d.w);
    if axis[0] * push[0] + axis[1] * push[1] + axis[2] * push[2] > 0.0 {
        // a turn about +a of the aim is one about -a of the canvas
        for v in &mut axis {
            *v = -*v;
        }
        angle = 2.0 * PI - angle;
    }
    let half = s * angle / 2.0;
    let k = half.sin();
    quat_normalized(quat_mul(from, Quat { x: axis[0] * k, y: axis[1] * k, z: axis[2] * k, w: half.cos() })).unwrap_or(to)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn mat3_round_trips_a_quat() {
        let q = quat_normalized(Quat { x: 0.1, y: -0.3, z: 0.2, w: 0.9 }).unwrap();
        let back = mat3_to_quat(&quat_to_mat3(q));
        assert!(quat_angle(q, back) < 1e-3);
    }

    #[test]
    fn aim_rotation_of_zero_is_identity() {
        assert_eq!(aim_rotation(0.0, 0.0), Quat::default());
        assert_eq!(view_from(Quat::default(), Quat::default()), IDENTITY);
    }
}
