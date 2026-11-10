//! The Publisher against the Rust reader: what the C++ `luma::Publisher` writes, sample by sample
//! (spike/driver/src/publish.cpp; its own tests are spike/driver/tests/publish_test.cpp).
mod common;

use common::*;
use xr_pose::feed::FeedReader;
use xr_pose::publish::{Pose, Publisher, PublisherOptions, Quat, history_step_for};

const ID: Quat = Quat {
    w: 1.0,
    x: 0.0,
    y: 0.0,
    z: 0.0,
};

fn opened(step: u32, pitch: f32) -> (Scratch, Publisher, FeedReader) {
    let scratch = Scratch::new();
    let options = PublisherOptions {
        feed: private(&scratch.prefix()),
        history_step: step,
        mount_pitch_deg: pitch,
        ..Default::default()
    };
    let publisher = Publisher::open(&options).unwrap();
    let reader = FeedReader::open(&scratch.prefix()).unwrap();
    (scratch, publisher, reader)
}

fn about_z(deg: f32) -> Quat {
    let h = deg.to_radians() / 2.0;
    Quat {
        w: h.cos(),
        x: 0.0,
        y: 0.0,
        z: h.sin(),
    }
}

fn pose(q: Quat, ms: u64) -> Pose {
    Pose {
        q,
        t_ns: ms * 1_000_000,
        calibrated: true,
    }
}

#[test]
fn history_step_follows_the_driver() {
    assert_eq!(
        [60, 90, 120, 240, 1000].map(history_step_for),
        [1, 1, 2, 4, 16]
    );
}

#[test]
fn bad_history_step_is_refused() {
    for step in [0, 1025] {
        let scratch = Scratch::new();
        let options = PublisherOptions {
            feed: private(&scratch.prefix()),
            history_step: step,
            ..Default::default()
        };
        assert!(Publisher::open(&options).is_err());
    }
}

#[test]
fn disabled_until_the_first_write_then_history_fills() {
    let (_s, mut p, mut r) = opened(2, 0.0);
    assert_eq!(r.read(), None);
    // The first sample: q1 and q2 repeat q0 and its t.
    assert!(p.publish(&pose(about_z(0.0), 100)));
    let s = r.read().unwrap();
    assert_eq!((s.device_ms, s.older_ms), (0.0, [0.0, 0.0]));
    assert_eq!(s.older, [[0.0, 0.0, 0.0, 1.0]; 2]);
    for (i, deg) in [10.0, 20.0, 30.0, 40.0, 50.0].into_iter().enumerate() {
        assert!(p.publish(&pose(about_z(deg), 100 + 16 * (i as u64 + 1))));
    }
    // Newest 50 deg at 80 ms; step back (2) 30 deg at 48 ms; twice that 10 deg at 16 ms.
    let s = r.read().unwrap();
    assert_eq!((s.device_ms, s.older_ms), (80.0, [48.0, 16.0]));
    let z = about_z(30.0).z;
    assert_eq!(s.older[0], [0.0, 0.0, z, about_z(30.0).w]);
    assert_eq!(s.older[1][2], about_z(10.0).z);
    assert_eq!(p.stats().written, 6);
}

#[test]
fn second_sample_with_step_one_has_real_history() {
    let (_s, mut p, mut r) = opened(1, 0.0);
    p.publish(&pose(ID, 0));
    p.publish(&pose(about_z(10.0), 16));
    let s = r.read().unwrap();
    assert_eq!((s.device_ms, s.older_ms), (16.0, [0.0, 0.0]));
    p.publish(&pose(about_z(20.0), 32));
    assert_eq!(r.read().unwrap().older_ms, [16.0, 0.0]);
}

#[test]
fn mount_pitch_is_turned_out_of_every_pose() {
    let (_s, mut p, mut r) = opened(1, -8.5);
    p.publish(&pose(ID, 0));
    let s = r.read().unwrap();
    let half = (-8.5f32).to_radians() / 2.0;
    let [x, y, z, w] = s.orientation;
    assert!((x).abs() < 1e-7 && (z).abs() < 1e-7, "{s:?}");
    assert!(
        (y - half.sin()).abs() < 1e-6 && (w - half.cos()).abs() < 1e-6,
        "{s:?}"
    );
}

#[test]
fn uncalibrated_pauses_and_bad_quaternions_drop() {
    let (_s, mut p, mut r) = opened(1, 0.0);
    assert!(p.publish(&pose(ID, 0)));
    assert!(r.read().is_some());
    assert!(!p.publish(&Pose {
        calibrated: false,
        ..pose(ID, 16)
    }));
    assert_eq!(r.read(), None);
    for q in [Quat { w: 0.0, ..ID }, Quat { w: f32::NAN, ..ID }] {
        assert!(!p.publish(&pose(q, 32)));
    }
    assert_eq!(p.stats().invalid, 2);
    assert_eq!(r.read(), None);
    // The clock of t is kept across the pause; the history is not.
    assert!(p.publish(&pose(ID, 48)));
    let s = r.read().unwrap();
    assert_eq!((s.device_ms, s.older_ms), (48.0, [48.0, 48.0]));
    p.pause();
    assert_eq!(r.read(), None);
}

#[test]
fn clock_restarts_when_it_goes_back_or_nears_two_to_the_24() {
    let (_s, mut p, mut r) = opened(1, 0.0);
    p.publish(&Pose {
        t_ns: 5_000_000_000,
        ..pose(ID, 0)
    });
    p.publish(&Pose {
        t_ns: 5_000_500_000,
        ..pose(ID, 0)
    });
    assert_eq!(r.read().unwrap().device_ms, 0.5, "fractional ms");
    p.publish(&Pose {
        t_ns: 1_000_000_000,
        ..pose(ID, 0)
    });
    assert_eq!(r.read().unwrap().device_ms, 0.0, "back: restart");
    let near = 1_000_000_000 + ((1u64 << 24) - 1000) * 1_000_000;
    p.publish(&Pose {
        t_ns: near,
        ..pose(ID, 0)
    });
    assert_eq!(r.read().unwrap().device_ms, 0.0, "2^24 - 1 s: restart");
    p.publish(&Pose {
        t_ns: near + 2_000_000,
        ..pose(ID, 0)
    });
    assert_eq!(r.read().unwrap().device_ms, 2.0);
}
