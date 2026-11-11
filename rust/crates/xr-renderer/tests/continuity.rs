//! The checks of ctest `continuity` (spike/tests/continuity_test.cpp), the C++ blobs of golden/continuity.cpp restored
//! here, and the Rust blobs it restores there (golden/continuity_{mid,end}_rust.bin; `XR_WRITE_GOLDENS=1` rewrites them,
//! then run `continuity-golden read` as golden/continuity.cpp says). Also `save` / `restore` over a memfd.

use std::path::PathBuf;

use xr_renderer::continuity::{restore, save, ViewContinuity};
use xr_renderer::pose::{aim_rotation, quat_angle, Change, FeedSample, Quat, ViewMode};
use xr_renderer::view::{EdgePan, TargetOptions, ViewAnimator};

fn sample(orientation: Quat, device_ms: f32) -> Option<FeedSample> {
    Some(FeedSample { orientation, device_ms, ..Default::default() })
}

fn pan_target() -> TargetOptions {
    TargetOptions { pan: true, ..Default::default() }
}

fn centred() -> TargetOptions {
    TargetOptions { center: true, ..Default::default() }
}

fn decoded(bytes: &[u8]) -> ViewContinuity {
    let mut c = ViewContinuity::default();
    assert!(c.decode(bytes));
    c
}

#[test]
fn continuity_test() {
    let mut a = ViewContinuity::default();
    a.pose.update(sample(Quat::default(), 1.0), 100.);
    a.pose.update(sample(aim_rotation(0.4, 0.1), 2.0), 120.);
    a.modes.turn_anchor(aim_rotation(0.2, 0.05));
    a.modes.update(a.pose.relative(), 120.);
    a.modes.set_mode(ViewMode::Follow, 130., Change::Maximize);
    a.maximized = true;
    a.view.set_target(true, 1, 70, 100.);
    a.view.advance(240.);
    a.rolls = [1250, -650, 20, 0];
    a.saved_ms = 240.;
    let mut b = ViewContinuity::default();
    assert!(b.decode(&a.encode().unwrap()));
    b.resume(3240.);
    assert!(b.maximized && b.modes.holding());
    assert_eq!(b.rolls, a.rolls);
    assert!(quat_angle(a.modes.anchor(), b.modes.anchor()) < 0.0001);
    let (ra, rb) = (a.pose.reference().unwrap(), b.pose.reference().unwrap());
    assert!(quat_angle(ra, rb) < 0.0001);
    assert_eq!(a.pose.view(), b.pose.view());
    b.view.advance(3240.);
    assert!((a.view.zoom() - b.view.zoom()).abs() < 0.0001);
    assert!(!b.decode(b"bad snapshot"));
    let mut corrupt = a.encode().unwrap();
    *corrupt.last_mut().unwrap() ^= 1;
    assert!(!b.decode(&corrupt));
    assert_eq!(b.rolls, a.rolls, "rejected snapshots do not partly replace state");

    // The pending recenter flag, and its learned up/roll reference, survive.
    a.pose.recenter();
    assert!(b.decode(&a.encode().unwrap()));
    b.resume(3240.);
    let head = sample(aim_rotation(0.7, 0.2), 3.0);
    a.pose.update(head, 250.);
    b.pose.update(head, 3250.);
    assert_eq!(a.pose.view(), b.pose.view());
    assert!(quat_angle(a.pose.reference().unwrap(), b.pose.reference().unwrap()) < 0.0001);

    // Resume a moving follow spring, then unmaximize back to the yaw anchor saved before maximize. Compare with an
    // uninterrupted reference timeline.
    a.modes.land_maximize(240.);
    let mut t = 240.;
    while t <= 400. {
        a.modes.update(aim_rotation(0.8, 0.1), t);
        t += 8.;
    }
    a.saved_ms = 400.;
    assert!(b.decode(&a.encode().unwrap()));
    b.resume(3400.);
    let mut t = 408.;
    while t <= 800. {
        let x = a.modes.update(aim_rotation(0.9, 0.1), t);
        let y = b.modes.update(aim_rotation(0.9, 0.1), t + 3000.);
        assert!(quat_angle(x, y) < 0.001);
        t += 8.;
    }
    a.modes.set_mode(ViewMode::Anchor, 800., Change::Restore);
    b.modes.set_mode(ViewMode::Anchor, 3800., Change::Restore);
    let o = head.unwrap().orientation;
    assert!(quat_angle(a.modes.update(o, 1200.), b.modes.update(o, 4200.)) < 0.001);

    // Freeze an edge pan, including its easing speed and timeout clock.
    a.view = ViewAnimator::default();
    a.view.set_target(true, 0, 40, 0.);
    a.view.advance(1000.);
    let pan = EdgePan {
        active: true,
        from: 0,
        to: 1,
        exit: 0.3,
        entry: 0.3,
        peek: 0.7,
        cross: 0.3,
        turn: 0.4,
        way: 2.0,
        zoom: 0.2,
        ..Default::default()
    };
    a.view.set_pan(&pan, 1000.);
    a.view.advance(1020.);
    a.saved_ms = 1020.;
    assert!(b.decode(&a.encode().unwrap()));
    b.resume(4020.);
    assert!(b.view.pan_active());
    assert_eq!(a.view.pan_cross(), b.view.pan_cross());
    assert_eq!(a.view.pan_turn(), b.view.pan_turn());
    a.view.set_target_with(true, 1, 40, 1030., pan_target());
    b.view.set_target_with(true, 1, 40, 4030., pan_target());
    let mut t = 1030.;
    while t < 2200. {
        a.view.advance(t);
        b.view.advance(t + 3000.);
        assert!((a.view.zoom() - b.view.zoom()).abs() < 0.0001);
        assert!((a.view.turn() - b.view.turn()).abs() < 0.0001);
        t += 8.;
    }
    // A turn waiting to be handed to the world anchor must land once, in order.
    let landed = a.view.take_landed_turns();
    let resumed_landed = b.view.take_landed_turns();
    assert!(!landed.is_empty() && landed.len() == resumed_landed.len());
    assert!(landed[0].from == resumed_landed[0].from && landed[0].to == resumed_landed[0].to);
    a.saved_ms = 2200.;
    // Capture another centred turn's serial and direction as well as its queue.
    a.view.set_target_with(true, 2, 40, 2200., centred());
    a.view.advance(4000.);
    a.saved_ms = 4000.;
    a.centre_valid = true;
    a.centre_serial = a.view.serial();
    a.centre_dir = [0.2, 0.3];
    assert!(b.decode(&a.encode().unwrap()));
    b.resume(7000.);
    assert!(b.centre_valid && b.centre_serial == a.centre_serial && b.centre_dir == a.centre_dir);
    assert!(!b.view.take_landed_turns().is_empty());
    assert!(b.view.take_landed_turns().is_empty());
    // Preserve the entire adaptive filter, including its double accumulator and gain.
    a.stabilization.strength(75);
    for i in 0..50 {
        a.stabilization.update(aim_rotation((i % 2) as f32 * 0.0001, 0.0), f64::from(i));
    }
    assert!(b.decode(&a.encode().unwrap()));
    for i in 50..100 {
        let q = aim_rotation((i % 2) as f32 * 0.0001, 0.0);
        let (x, y) = (a.stabilization.update(q, f64::from(i)), b.stabilization.update(q, f64::from(i)));
        assert!(x.x == y.x && x.y == y.y && x.z == y.z && x.w == y.w);
        assert_eq!(a.stabilization.prediction_gain(), b.stabilization.prediction_gain());
    }
}

/// golden/continuity.cpp's `mid`, replayed on the Rust ports.
fn mid() -> ViewContinuity {
    let mut a = ViewContinuity::default();
    a.pose.update(sample(Quat::default(), 1.0), 100.);
    a.pose.update(sample(aim_rotation(0.4, 0.1), 2.0), 120.);
    a.modes.turn_anchor(aim_rotation(0.2, 0.05));
    a.modes.update(a.pose.relative(), 120.);
    a.modes.set_mode(ViewMode::Follow, 130., Change::Maximize);
    a.maximized = true;
    a.view.set_target(true, 1, 70, 100.);
    a.view.advance(240.);
    a.rolls = [1250, -650, 20, 0];
    a.pose.recenter();
    a.pose.update(sample(aim_rotation(0.7, 0.2), 3.0), 250.);
    a.modes.land_maximize(240.);
    let mut t = 240.;
    while t <= 400. {
        a.modes.update(aim_rotation(0.8, 0.1), t);
        t += 8.;
    }
    a.view = ViewAnimator::default();
    a.view.set_target(true, 0, 40, 0.);
    a.view.advance(1000.);
    let pan = EdgePan {
        active: true,
        from: 0,
        to: 1,
        side: 3,
        exit: 0.3,
        entry: 0.3,
        peek: 0.7,
        cross: 0.3,
        turn: 0.4,
        way: 2.0,
        zoom: 0.2,
    };
    a.view.set_pan(&pan, 1000.);
    a.view.advance(1020.);
    a.last_drawn = [0., 1., 0., -1., 0., 0., 0., 0., 1.];
    a.stabilization.strength(75);
    for i in 0..50 {
        a.stabilization.update(aim_rotation((i % 2) as f32 * 0.0001, 0.0), f64::from(970 + i));
    }
    a.saved_ms = 1020.;
    a
}

/// golden/continuity.cpp's `end`.
fn end() -> ViewContinuity {
    let mut a = mid();
    a.view.set_target_with(true, 1, 40, 1030., pan_target());
    let mut t = 1030.;
    while t < 2200. {
        a.view.advance(t);
        t += 8.;
    }
    a.view.set_target_with(true, 2, 40, 2200., centred());
    a.view.advance(4000.);
    a.view.set_target_with(false, 3, 55, 4000., centred());
    a.view.advance(4100.);
    a.modes.set_mode(ViewMode::Anchor, 4000., Change::Restore);
    a.modes.update(aim_rotation(0.9, 0.1), 4100.);
    a.centre_valid = true;
    a.centre_serial = a.view.serial();
    a.centre_dir = [0.2, 0.3];
    a.saved_ms = 4100.;
    a
}

fn golden(name: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("golden").join(name)
}

/// `rust` and `cpp` agree on every byte but padding and the hash (which covers the C++ padding).
fn same_but_padding(rust: &ViewContinuity, cpp: &[u8], what: &str) {
    let bytes = rust.encode().unwrap();
    let padding = rust.padding().unwrap();
    assert_eq!(bytes.len(), cpp.len(), "{what}: length");
    assert_eq!(bytes[..8], cpp[..8], "{what}: magic");
    for i in 16..bytes.len() {
        assert!(padding[i] || bytes[i] == cpp[i], "{what}: payload byte {} differs", i - 16);
    }
}

#[test]
fn cpp_blobs_restore_and_rust_blobs_are_current() {
    for (name, replay) in [("mid", mid as fn() -> ViewContinuity), ("end", end)] {
        let cpp = std::fs::read(golden(&format!("continuity_{name}.bin"))).unwrap();
        let restored = decoded(&cpp);
        // Every value the C++ wrote lands in the field the C++ wrote it from.
        same_but_padding(&restored, &cpp, name);
        // And the Rust ports, run through the same scenario, reach that very state.
        let replayed = replay();
        assert_eq!(replayed.encode(), restored.encode(), "{name}: Rust replay");
        // The C++ checks continuity_{name}_rust.bin restores there (golden/continuity.cpp read).
        let path = golden(&format!("continuity_{name}_rust.bin"));
        let rust = restored.encode().unwrap();
        if std::env::var_os("XR_WRITE_GOLDENS").is_some() {
            std::fs::write(&path, &rust).unwrap();
        }
        assert_eq!(std::fs::read(&path).unwrap(), rust, "{name}: Rust blob is current");
        assert_eq!(decoded(&rust).encode().unwrap(), rust, "{name}: Rust round trip");
    }
}

#[test]
fn padding_is_only_padding() {
    // The C++ layout's holes (x86-64 GCC): a value's own bytes are never padding, so every value is compared above.
    let c = end();
    let padding = c.padding().unwrap();
    assert_eq!(padding.len(), c.encode().unwrap().len());
    assert_eq!(padding.iter().filter(|p| **p).count(), 54);
}

#[test]
fn rejects() {
    let mut b = ViewContinuity::default();
    b.rolls = [7, 0, 0, 0];
    let good = end().encode().unwrap();
    assert!(!b.decode(&good[..good.len() - 1]), "short");
    assert!(!b.decode(&[good.clone(), vec![0]].concat()), "long");
    let mut magic = good.clone();
    magic[7] = b'2';
    assert!(!b.decode(&magic), "magic");
    assert!(!b.decode(&good[..15]), "under 16 bytes");
    assert!(!b.decode(&vec![0; 65537]), "over 64 KiB");
    assert_eq!(b.rolls, [7, 0, 0, 0]);
    // More than 64 landed turns: the C++ throws, so nothing is written.
    let mut many = end();
    for i in 0..70u32 {
        many.view.set_target_with(true, i % 3, 40, 5000. + f64::from(i) * 1000., centred());
        many.view.advance(5000. + f64::from(i) * 1000. + 999.);
    }
    assert!(many.clone().view.take_landed_turns().len() > 64);
    assert!(many.encode().is_none());
}

#[test]
fn save_and_restore_over_a_file() {
    use rustix::fs::{MemfdFlags, fstat, memfd_create};
    use std::os::fd::AsFd;
    let fd = memfd_create("continuity", MemfdFlags::CLOEXEC).unwrap();
    assert!(restore(fd.as_fd(), 0.).is_none(), "empty");
    let c = end();
    save(fd.as_fd(), &c);
    assert_eq!(fstat(&fd).unwrap().st_size as usize, c.encode().unwrap().len());
    let r = restore(fd.as_fd(), 5100.).unwrap();
    assert_eq!(fstat(&fd).unwrap().st_size, 0, "consumed once");
    assert_eq!(r.saved_ms, 5100.);
    let mut resumed = c.clone();
    resumed.resume(5100.);
    assert_eq!(r.encode(), resumed.encode());
    assert!(restore(fd.as_fd(), 5200.).is_none(), "no second restore");
    // The C++ blob, through the file.
    let cpp = std::fs::read(golden("continuity_end.bin")).unwrap();
    rustix::io::pwrite(&fd, &cpp, 0).unwrap();
    assert!(restore(fd.as_fd(), 4100.).is_some());
}
