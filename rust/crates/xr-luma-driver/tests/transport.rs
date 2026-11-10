//! The usbfs and discovery tests of `spike/driver/tests/transport_test.cpp`, run on the Rust transport
//! over the same fake kernel. The hidraw tests stay with the C++ driver: the Rust one has no hidraw
//! backend. Those that test the read loop rather than hidraw (interrupt, close, events, signals) run
//! here on usbfs instead.

mod common;

use std::alloc::{GlobalAlloc, Layout, System};
use std::cell::Cell;
use std::ffi::c_int;
use std::os::fd::RawFd;
use std::path::Path;
use std::sync::atomic::{AtomicI32, AtomicUsize, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant};

use common::*;
use xr_luma_driver::transport::{
    EventKind, IoStatus, LUMA_ULTRA_DATA_IN, LUMA_ULTRA_DATA_OUT, Transport, TransportConfig, allowed_mcu_frame,
    find_device,
};
use xr_luma_driver::usbdevfs::{self, Errno, LibcSys, Request, Sys};

// ---------------------------------------------------------------- allocation count

struct Counting;

thread_local! {
    static ALLOCS: Cell<u64> = const { Cell::new(0) };
}

fn count_one() {
    let _ = ALLOCS.try_with(|n| n.set(n.get() + 1));
}

// SAFETY: forwards to the system allocator; the count is a thread-local with no allocation of its own.
unsafe impl GlobalAlloc for Counting {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        count_one();
        unsafe { System.alloc(layout) }
    }
    unsafe fn alloc_zeroed(&self, layout: Layout) -> *mut u8 {
        count_one();
        unsafe { System.alloc_zeroed(layout) }
    }
    unsafe fn realloc(&self, ptr: *mut u8, layout: Layout, new_size: usize) -> *mut u8 {
        count_one();
        unsafe { System.realloc(ptr, layout, new_size) }
    }
    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        unsafe { System.dealloc(ptr, layout) }
    }
}

#[global_allocator]
static ALLOCATOR: Counting = Counting;

fn allocations() -> u64 {
    ALLOCS.with(Cell::get)
}

/// The C++ write() default.
const WRITE: Duration = Duration::from_millis(1000);

const IMU: [u8; 28] = [
    0xaa, 0x8f, 0x00, 0x88, 0x3c, 0x15, 0x10, 0x00, 0xdd, 0x1e, 0x01, 0x00, 0xf4, 0xff, 0x05, 0x00, 0x24, 0x00, 0x15, 0xff,
    0x11, 0xf8, 0x74, 0x1f, 0xe8, 0x06, 0x00, 0x00,
];

fn open_usb(config: impl FnOnce(&FakeTree) -> TransportConfig) -> (Arc<FakeUsbfs>, Transport, Buffer) {
    let (fake, mut t) = connected_usb(config);
    t.open();
    let mut buffer = [0u8; 2048];
    t.read(&mut buffer, ms(100));
    (fake, t, buffer)
}

// ---------------------------------------------------------------- discovery

#[test]
fn finds_usb_device_and_endpoints() {
    let tree = FakeTree::new();
    tree.add_usb("5-1", "1532", "0574", 2, &[], 0);
    tree.add_usb("5-2.1", "35ca", "1104", 101, &ENDPOINTS, 0);
    let found = find_device(&usb_config(&tree)).expect("found");
    assert_eq!(found.sys_name, "5-2.1");
    assert_eq!(found.node, tree.dev().join("bus/usb/005/101"));
    assert_eq!(found.endpoints.len(), 6);
    let bulk = |address: u8| found.endpoints.iter().find(|e| e.address == address).map(|e| e.bulk);
    assert_eq!(bulk(0x81), Some(false));
    assert_eq!(bulk(0x82), Some(true));
    assert_eq!(bulk(0x04), Some(true));
    for e in &found.endpoints {
        assert_eq!(e.max_packet, if e.address == 0x81 { 1024 } else { 512 });
    }
}

#[test]
fn finds_nothing_for_absent_device() {
    let tree = FakeTree::new();
    tree.add_usb("5-1", "1532", "0574", 2, &[], 0);
    assert!(find_device(&usb_config(&tree)).is_none());
}

#[test]
fn finds_nothing_when_interface_absent() {
    let tree = FakeTree::new();
    tree.add_usb("5-2.1", "35ca", "1104", 101, &ENDPOINTS, 0);
    let config = TransportConfig { interface_number: 1, ..usb_config(&tree) };
    assert!(find_device(&config).is_none());
}

#[test]
fn finds_real_shaped_paths_only_through_given_roots() {
    let tree = FakeTree::new(); // An empty fake root must not fall through to the machine's real /sys.
    assert!(find_device(&usb_config(&tree)).is_none());
}

// ---------------------------------------------------------------- the read loop

#[test]
fn read_times_out_on_time() {
    let _serial = serial();
    let (_fake, mut t, mut buffer) = open_usb(usb_config);
    let start = Instant::now();
    assert_eq!(t.read(&mut buffer, ms(50)).status, IoStatus::Timeout);
    let took = ms_since(start);
    assert!((45..300).contains(&took), "{took} ms");
}

#[test]
fn no_device_at_start_connects_when_it_arrives() {
    let _serial = serial();
    let fake = FakeUsbfs::new();
    let mut t = Transport::with_sys(usb_config(fake.tree()), fake.clone());
    assert!(!t.open());
    assert!(!t.connected());
    assert!(!t.last_error().is_empty());
    let mut buffer = [0u8; 2048];
    assert_eq!(t.read(&mut buffer, ms(30)).status, IoStatus::Timeout);
    fake.arrive(101);
    assert_eq!(read_until(&mut t, &mut buffer, IoStatus::Connected, ms(1000)).status, IoStatus::Connected);
}

#[test]
fn interrupt_wakes_a_blocked_read() {
    let _serial = serial();
    let (_fake, mut t, mut buffer) = open_usb(usb_config);
    let interrupter = t.interrupter();
    let waker = thread::spawn(move || {
        thread::sleep(ms(30));
        interrupter.interrupt();
    });
    let start = Instant::now();
    assert_eq!(t.read(&mut buffer, ms(5000)).status, IoStatus::Interrupted);
    assert!(ms_since(start) < 1000);
    waker.join().unwrap();
    assert_eq!(t.read(&mut buffer, ms(0)).status, IoStatus::Timeout); // One wake, one Interrupted.
}

#[test]
fn interrupt_wakes_a_read_while_disconnected() {
    let _serial = serial();
    let fake = FakeUsbfs::new();
    let mut t = Transport::with_sys(usb_config(fake.tree()), fake.clone());
    t.open();
    let interrupter = t.interrupter();
    let waker = thread::spawn(move || {
        thread::sleep(ms(30));
        interrupter.interrupt();
    });
    let mut buffer = [0u8; 2048];
    let start = Instant::now();
    assert_eq!(t.read(&mut buffer, ms(5000)).status, IoStatus::Interrupted);
    assert!(ms_since(start) < 1000);
    waker.join().unwrap();
}

#[test]
fn huge_timeouts_mean_a_long_wait() {
    let _serial = serial();
    let (_fake, mut t, mut buffer) = open_usb(usb_config);
    let interrupter = t.interrupter();
    let waker = thread::spawn(move || {
        thread::sleep(ms(30));
        interrupter.interrupt();
    });
    assert_eq!(t.read(&mut buffer, Duration::MAX).status, IoStatus::Interrupted);
    waker.join().unwrap();
}

#[test]
fn close_stops_reads_and_open_reconnects() {
    let _serial = serial();
    let fake = FakeUsbfs::new();
    fake.arrive(101);
    let before = open_fd_count();
    {
        let mut t = Transport::with_sys(usb_config(fake.tree()), fake.clone());
        let mut buffer = [0u8; 2048];
        assert_eq!(t.read(&mut buffer, ms(0)).status, IoStatus::Closed); // open() not called yet.
        t.open();
        t.close();
        assert!(!t.connected());
        assert_eq!(t.read(&mut buffer, ms(0)).status, IoStatus::Closed);
        assert_eq!(t.write(&[1], WRITE), IoStatus::Closed);
        assert!(t.open());
        assert_eq!(t.read(&mut buffer, ms(0)).status, IoStatus::Connected);
    }
    assert_eq!(open_fd_count(), before);
}

#[test]
fn write_while_down_says_so_and_queues_nothing() {
    let _serial = serial();
    let (fake, mut t, mut buffer) = open_usb(usb_config);
    fake.yank();
    assert_eq!(read_until(&mut t, &mut buffer, IoStatus::Disconnected, ms(1000)).status, IoStatus::Disconnected);
    assert_eq!(t.write(&[1, 2, 3], WRITE), IoStatus::Disconnected);
    fake.arrive(102);
    assert_eq!(read_until(&mut t, &mut buffer, IoStatus::Connected, ms(1000)).status, IoStatus::Connected);
    assert!(fake.sent().is_empty()); // The refused write did not come back to life.
}

#[test]
fn events_tell_the_story_once() {
    let _serial = serial();
    let (fake, mut t, mut buffer) = open_usb(usb_config);
    fake.yank();
    read_until(&mut t, &mut buffer, IoStatus::Disconnected, ms(1000));
    thread::sleep(ms(40));
    fake.arrive(102);
    read_until(&mut t, &mut buffer, IoStatus::Connected, ms(1000));

    let e = t.next_event().expect("connected");
    assert!(e.kind == EventKind::Connected && e.downtime_ms == 0);
    let e = t.next_event().expect("disconnected");
    assert!(e.kind == EventKind::Disconnected && !e.reason.is_empty() && e.t_ns != 0);
    let e = t.next_event().expect("connected again");
    assert!(e.kind == EventKind::Connected && (30..1000).contains(&e.downtime_ms), "{e:?}");
    assert!(t.next_event().is_none());
    assert!(t.stats().last_downtime_ms >= 30);
}

/// A signal handler's plain flag must get a chance to be seen: poll's EINTR comes back as Interrupted.
#[test]
fn signal_interrupts_a_blocked_read() {
    extern "C" fn nothing(_: c_int) {}
    let _serial = serial();
    let (_fake, mut t, mut buffer) = open_usb(usb_config);
    // SAFETY: plain sigaction setup; the handler does nothing, and the old one is put back.
    let mut old: libc::sigaction = unsafe { std::mem::zeroed() };
    unsafe {
        let mut action: libc::sigaction = std::mem::zeroed();
        action.sa_sigaction = nothing as extern "C" fn(c_int) as usize;
        libc::sigemptyset(&mut action.sa_mask);
        libc::sigaction(libc::SIGUSR1, &action, &mut old);
    }
    // SAFETY: no arguments.
    let me = unsafe { libc::pthread_self() };
    let signaller = thread::spawn(move || {
        thread::sleep(ms(30));
        // SAFETY: me is the test thread, alive until join below.
        unsafe { libc::pthread_kill(me, libc::SIGUSR1) };
    });
    let start = Instant::now();
    assert_eq!(t.read(&mut buffer, ms(5000)).status, IoStatus::Interrupted);
    assert!(ms_since(start) < 1000);
    signaller.join().unwrap();
    // SAFETY: restores what was there.
    unsafe { libc::sigaction(libc::SIGUSR1, &old, std::ptr::null_mut()) };
}

// ---------------------------------------------------------------- usbfs

#[test]
fn usbfs_claims_then_keeps_urbs_in_flight_on_every_in_endpoint() {
    let _serial = serial();
    let (fake, mut t) = connected_usb(usb_config);
    assert!(t.open());
    assert!(t.device().is_some());
    assert_eq!(fake.claims(), 1);
    assert_eq!(fake.claimed_interface(), 0);
    assert_eq!(fake.inflight(), 4); // 2 URBs x 2 endpoints.
    assert_ne!(fake.open_flags() & libc::O_CLOEXEC, 0);
    assert_eq!(fake.open_flags() & libc::O_ACCMODE, libc::O_RDWR);
    let mut buffer = [0u8; 2048];
    assert_eq!(t.read(&mut buffer, ms(100)).status, IoStatus::Connected);
}

#[test]
fn usbfs_stream_keeps_order_and_names_the_endpoint() {
    let _serial = serial();
    let (fake, mut t, mut buffer) = open_usb(usb_config);
    let taken = Arc::new(AtomicUsize::new(0));
    let device = {
        let fake = fake.clone();
        let taken = taken.clone();
        thread::spawn(move || {
            for i in 0..2000usize {
                // Stays within the ring's reach: a real device has a fixed rate, this one has none.
                while i.saturating_sub(taken.load(Ordering::Relaxed)) > 100 {
                    thread::yield_now();
                }
                let end = Instant::now() + ms(1000);
                while !fake.deliver(0x81, &[i as u8, (i >> 8) as u8]) && Instant::now() < end {
                    thread::yield_now();
                }
            }
        })
    };
    let mut expected = 0usize;
    let mut in_order = true;
    let end = Instant::now() + ms(5000);
    while expected < 2000 && Instant::now() < end {
        let r = t.read(&mut buffer, ms(50));
        if r.status != IoStatus::Ok {
            continue;
        }
        assert_eq!(r.endpoint, 0x81);
        in_order &= (usize::from(buffer[0]) | usize::from(buffer[1]) << 8) == expected;
        expected += 1;
        taken.store(expected, Ordering::Relaxed);
    }
    device.join().unwrap();
    assert_eq!(expected, 2000);
    assert!(in_order);

    fake.deliver(0x82, &[7, 7, 7]);
    let r = t.read(&mut buffer, ms(500));
    assert!(r.status == IoStatus::Ok && r.endpoint == 0x82 && r.bytes == 3, "{r:?}");
    assert_eq!(t.stats().dropped_packets, 0);
}

#[test]
fn usbfs_ignores_empty_packets_but_keeps_reading() {
    let _serial = serial();
    let (fake, mut t, mut buffer) = open_usb(usb_config);
    fake.deliver(0x82, &[]);
    assert_eq!(t.read(&mut buffer, ms(30)).status, IoStatus::Timeout);
    assert_eq!(fake.inflight(), 4);
}

#[test]
fn usbfs_rejects_endpoints_the_device_does_not_have() {
    let _serial = serial();
    let (fake, mut t) = connected_usb(|tree| TransportConfig { in_endpoints: vec![0x8f], ..usb_config(tree) });
    assert!(!t.open());
    assert!(t.last_error().contains("0x8f"), "{}", t.last_error());
    assert_eq!(fake.claims(), 0);
    assert!(!fake.descriptor_open());
}

/// 0x04/0x85 also carry firmware updates, so a wrong endpoint in a config must not reach them.
#[test]
fn usbfs_refuses_the_flash_channel_on_the_ultra() {
    let _serial = serial();
    for out in [true, false] {
        let (fake, mut t) = connected_usb(|tree| {
            let mut config = usb_config(tree);
            if out {
                config.out_endpoint = 0x04;
            } else {
                config.in_endpoints = vec![0x85];
            }
            config
        });
        assert!(!t.open());
        assert!(t.last_error().contains("firmware"), "{}", t.last_error());
        assert_eq!(fake.claims(), 0);
        assert!(!fake.descriptor_open());
    }
}

#[test]
fn mcu_channel_passes_only_allowlisted_frames() {
    let _serial = serial();
    assert!(allowed_mcu_frame(&[0xfa, 0x55, 0x95, 0x00, 0x00]));
    assert!(allowed_mcu_frame(&[0xfa, 0x55, 0x97, 0x00, 0x00]));
    let (fake, mut t) = connected_usb(live_config);
    assert!(t.open());
    assert_eq!(t.write(&[0xfa, 0x55, 0x95, 0x00, 0x00], WRITE), IoStatus::Ok);
    assert_eq!(fake.sent().len(), 1);
    let refused: [&[u8]; 9] = [
        &[],
        &[0xfa, 0x55],
        &[0xfa, 0x55, 0x95, 0x00],                   // cut short
        &[0xfa, 0x55, 0x95, 0x00, 0x00, 0x00],       // one byte long
        &[0xfa, 0x55, 0x95, 0x00, 0x01, 0x00],       // with a payload
        &[0xfa, 0x55, 0x96, 0x00, 0x00],             // a neighbouring id: exactly the guess this list is here to stop
        &[0xfa, 0x55, 0x90, 0x00, 0x02, 0x06, 0xc0], // captured, but exposure control has no place here
        &[0xfa, 0x55, 0xe5, 0x01, 0x00],             // captured, but it reads calibration
        &[0xff, 0xfe, 0x00, 0x00],
    ];
    for frame in refused {
        assert_eq!(t.write(frame, WRITE), IoStatus::Error, "{frame:02x?}");
        assert!(t.last_error().contains("allowlist"), "{}", t.last_error());
    }
    assert_eq!(fake.sent().len(), 1); // none of them reached the device
    assert!(t.connected());
}

#[test]
fn live_path_with_captured_packets() {
    let _serial = serial();
    let (fake, mut t) = connected_usb(live_config);
    assert!(t.open());
    let mut buffer = [0u8; 1024];
    assert_eq!(t.read(&mut buffer, ms(100)).status, IoStatus::Connected);
    assert_eq!(t.write(&[0xfa, 0x55, 0x95, 0x00, 0x00], WRITE), IoStatus::Ok);
    assert!(fake.deliver(0x85, &[0xfa, 0x55, 0x95, 0x00, 0x00, 0x00])); // captured reply
    assert!(fake.deliver(0x81, &IMU));
    let reply = t.read(&mut buffer, ms(100));
    assert!(reply.status == IoStatus::Ok && reply.endpoint == 0x85 && reply.bytes == 6, "{reply:?}");
    let sample = t.read(&mut buffer, ms(100));
    assert!(sample.status == IoStatus::Ok && sample.endpoint == 0x81 && sample.bytes == IMU.len(), "{sample:?}");
    assert_eq!(buffer[..IMU.len()], IMU);
}

#[test]
fn write_to_reaches_only_configured_endpoints() {
    let _serial = serial();
    let (fake, mut t) = connected_usb(|tree| {
        let mut config = live_config(tree);
        config.extra_out_endpoints = vec![0x06];
        config.in_endpoints.push(0x87);
        config
    });
    assert!(t.open());
    assert_eq!(t.write_to(0x06, &[1; 512], WRITE), IoStatus::Ok);
    assert_eq!(t.write_to(0x02, &[1], WRITE), IoStatus::Error);
    assert_eq!(t.write_to(0x04, &[0xfa, 0x55, 0x90, 0x00, 0x00], WRITE), IoStatus::Error); // still the allowlist
    assert_eq!(t.write_to(0x04, &[0xfa, 0x55, 0x97, 0x00, 0x00], WRITE), IoStatus::Ok);
    assert_eq!(fake.sent().len(), 2);
}

#[test]
fn drain_endpoint_reads_and_throws_away() {
    let _serial = serial();
    let (fake, mut t) = connected_usb(|tree| TransportConfig {
        drain_endpoints: vec![0x82],
        drain_urbs: 3,
        drain_urb_bytes: 16384,
        ..live_config(tree)
    });
    assert!(t.open());
    let mut buffer = [0u8; 1024];
    assert_eq!(t.read(&mut buffer, ms(100)).status, IoStatus::Connected);
    assert!(fake.deliver(0x82, &vec![9; 16384]));
    assert_eq!(t.read(&mut buffer, ms(30)).status, IoStatus::Timeout); // nothing for the reader
    let on_drain = fake.submitted_endpoints().iter().filter(|&&e| e == 0x82).count();
    assert_eq!(on_drain, 4); // 3 queued, and the finished one went straight back
    assert!(t.connected());
}

#[test]
fn usbfs_needs_an_in_endpoint() {
    let _serial = serial();
    let (fake, mut t) = connected_usb(|tree| TransportConfig { in_endpoints: vec![], ..usb_config(tree) });
    assert!(!t.open());
    assert!(t.last_error().contains("endpoint"), "{}", t.last_error());
    assert_eq!(fake.claims(), 0);
}

#[test]
fn usbfs_waits_for_a_busy_interface_and_never_takes_it() {
    let _serial = serial();
    let (fake, mut t) = connected_usb(usb_config);
    fake.set_busy(true);
    assert!(!t.open());
    let error = t.last_error().to_lowercase();
    assert!(error.contains("busy"), "{error}");
    assert!(!fake.descriptor_open()); // Failed claim: nothing left open.
    let mut buffer = [0u8; 2048];
    assert_eq!(t.read(&mut buffer, ms(40)).status, IoStatus::Timeout);
    assert!(t.stats().open_failures >= 2);
    fake.set_busy(false);
    assert_eq!(read_until(&mut t, &mut buffer, IoStatus::Connected, ms(1000)).status, IoStatus::Connected);
}

#[test]
fn usbfs_retries_while_udev_applies_permissions() {
    let _serial = serial();
    let (fake, mut t) = connected_usb(usb_config);
    fake.fail_opens(3, libc::EACCES);
    assert!(!t.open());
    let mut buffer = [0u8; 2048];
    assert_eq!(read_until(&mut t, &mut buffer, IoStatus::Connected, ms(1000)).status, IoStatus::Connected);
}

#[test]
fn usbfs_disconnect_mid_read_then_reconnect() {
    let _serial = serial();
    let (fake, mut t, mut buffer) = open_usb(usb_config);
    fake.deliver(0x81, &[1]);
    assert_eq!(t.read(&mut buffer, ms(200)).status, IoStatus::Ok);
    fake.deliver(0x81, &[2]); // Arrives just before the drop: must still reach the reader.
    let yank = {
        let fake = fake.clone();
        thread::spawn(move || {
            thread::sleep(ms(30));
            fake.yank();
        })
    };
    let r = t.read(&mut buffer, ms(500));
    assert!(r.status == IoStatus::Ok && buffer[0] == 2, "{r:?}");
    let start = Instant::now();
    let r = t.read(&mut buffer, ms(5000));
    yank.join().unwrap();
    assert_eq!(r.status, IoStatus::Disconnected);
    assert!(ms_since(start) < 1000);
    assert!(!fake.descriptor_open());
    assert_eq!(t.read(&mut buffer, ms(5)).status, IoStatus::Timeout); // Disconnected is reported once.

    thread::sleep(ms(60));
    fake.arrive(102); // Same port, new device number, as after a real re-enumeration.
    let start = Instant::now();
    assert_eq!(read_until(&mut t, &mut buffer, IoStatus::Connected, ms(1000)).status, IoStatus::Connected);
    assert!(ms_since(start) < 1000);
    assert_eq!(t.device().map(|d| d.node.clone()), Some(fake.tree().dev().join("bus/usb/005/102")));
    assert_eq!(fake.inflight(), 4); // URBs are back up without being asked.
    assert_eq!(fake.claims(), 2);
    fake.deliver(0x81, &[3]);
    let r = t.read(&mut buffer, ms(500));
    assert!(r.status == IoStatus::Ok && buffer[0] == 3, "{r:?}");
    assert_eq!(t.stats().disconnects, 1);
}

/// Drops come in bursts seconds apart. Nothing may pile up.
#[test]
fn usbfs_survives_a_burst_of_drops_without_leaking() {
    let _serial = serial();
    let (fake, mut t, mut buffer) = open_usb(usb_config);
    let fds = open_fd_count();
    for i in 0..30 {
        fake.yank();
        assert_eq!(read_until(&mut t, &mut buffer, IoStatus::Disconnected, ms(500)).status, IoStatus::Disconnected);
        fake.arrive(102 + i);
        assert_eq!(read_until(&mut t, &mut buffer, IoStatus::Connected, ms(500)).status, IoStatus::Connected);
        assert_eq!(fake.inflight(), 4);
    }
    assert_eq!(open_fd_count(), fds);
    assert_eq!(t.stats().disconnects, 30);
    assert_eq!(t.stats().connects, 31);
}

#[test]
fn usbfs_write_goes_out_once_with_the_right_type() {
    let _serial = serial();
    let (fake, mut t) = connected_usb(usb_config);
    t.open();
    assert_eq!(t.write(&[0xff, 0xfe, 1], WRITE), IoStatus::Ok);
    assert_eq!(fake.sent(), vec![vec![0xff, 0xfe, 1]]);
    assert_eq!(fake.submitted_endpoints().iter().filter(|&&e| e == LUMA_ULTRA_DATA_OUT).count(), 1);
}

#[test]
fn usbfs_write_timeout_cancels_and_does_not_retry_or_drop_the_link() {
    let _serial = serial();
    let (fake, mut t, _buffer) = open_usb(usb_config);
    fake.hold_out_urbs(true);
    let start = Instant::now();
    assert_eq!(t.write(&[1], ms(40)), IoStatus::Timeout);
    assert!(ms_since(start) < 500);
    assert!(t.connected());
    assert_eq!(fake.request_count(usbdevfs::DISCARDURB), 1);
    assert_eq!(fake.inflight(), 4); // Only the IN URBs remain.
    assert_eq!(fake.submitted_endpoints().iter().filter(|&&e| e == LUMA_ULTRA_DATA_OUT).count(), 1); // One try.
    fake.hold_out_urbs(false);
    assert_eq!(t.write(&[2], WRITE), IoStatus::Ok);
}

#[test]
fn usbfs_write_keeps_packets_that_arrive_meanwhile() {
    let _serial = serial();
    let (fake, mut t, mut buffer) = open_usb(usb_config);
    fake.hold_out_urbs(true);
    let device = {
        let fake = fake.clone();
        thread::spawn(move || {
            thread::sleep(ms(10));
            fake.deliver(0x81, &[42]);
        })
    };
    t.write(&[1], ms(40));
    device.join().unwrap();
    let r = t.read(&mut buffer, ms(100));
    assert!(r.status == IoStatus::Ok && buffer[0] == 42, "{r:?}");
    assert_eq!(fake.inflight(), 4);
}

/// write() sends one packet, so a larger buffer is refused rather than split in two.
#[test]
fn usbfs_write_refuses_more_than_one_packet_and_missing_out_endpoint() {
    let _serial = serial();
    let (fake, mut t) = connected_usb(usb_config);
    t.open();
    assert_eq!(t.write(&[1; 513], WRITE), IoStatus::Error);
    assert!(fake.sent().is_empty());
    assert!(t.connected());
    assert_eq!(t.write(&[1; 512], WRITE), IoStatus::Ok);

    let (_fake2, mut t2) = connected_usb(|tree| TransportConfig { out_endpoint: 0, ..usb_config(tree) });
    t2.open();
    assert_eq!(t2.write(&[1], WRITE), IoStatus::Error);
}

#[test]
fn usbfs_write_during_a_drop_reports_disconnected() {
    let _serial = serial();
    let (fake, mut t, mut buffer) = open_usb(usb_config);
    fake.yank();
    assert_eq!(t.write(&[1], WRITE), IoStatus::Disconnected);
    assert!(!t.connected());
    // read still hears of it.
    assert_eq!(read_until(&mut t, &mut buffer, IoStatus::Disconnected, ms(500)).status, IoStatus::Disconnected);
}

#[test]
fn usbfs_full_queue_drops_oldest_and_counts_it() {
    let _serial = serial();
    let (fake, mut t, mut buffer) = open_usb(|tree| TransportConfig { queue_frames: 4, ..usb_config(tree) });
    // A slow reader: packets land while write() pumps the queue.
    fake.hold_out_urbs(true);
    let device = {
        let fake = fake.clone();
        thread::spawn(move || {
            for i in 0..6u8 {
                let end = Instant::now() + ms(500);
                while !fake.deliver(0x81, &[i]) && Instant::now() < end {
                    thread::yield_now();
                }
            }
        })
    };
    t.write(&[1], ms(100));
    device.join().unwrap();
    assert_eq!(t.stats().dropped_packets, 2);
    let r = t.read(&mut buffer, ms(100));
    assert!(r.status == IoStatus::Ok && buffer[0] == 2, "{r:?}"); // 0 and 1 are gone; the newest survive.
}

#[test]
fn usbfs_flags_a_truncated_packet() {
    let _serial = serial();
    let (fake, mut t) = connected_usb(usb_config);
    t.open();
    let mut small = [0u8; 4];
    t.read(&mut small, ms(100));
    fake.deliver(0x81, &[1, 2, 3, 4, 5, 6]);
    let r = t.read(&mut small, ms(500));
    assert!(r.status == IoStatus::Ok && r.bytes == 4 && r.truncated, "{r:?}");
}

/// A zero-length packet is legal on USB.
#[test]
fn usbfs_empty_write_and_empty_read_buffer() {
    let _serial = serial();
    let (fake, mut t, _buffer) = open_usb(usb_config);
    assert_eq!(t.write(&[], WRITE), IoStatus::Ok);
    assert_eq!(fake.sent(), vec![Vec::<u8>::new()]);
    fake.deliver(0x81, &[1, 2]);
    let r = t.read(&mut [], ms(500));
    assert!(r.status == IoStatus::Ok && r.bytes == 0 && r.truncated, "{r:?}");
}

/// The data channel's frames are exactly one packet. A URB bigger than that would hold each frame
/// back until the next one arrives.
#[test]
fn usbfs_full_size_packet_arrives_at_once() {
    let _serial = serial();
    let (fake, mut t, mut buffer) =
        open_usb(|tree| TransportConfig { in_endpoints: vec![LUMA_ULTRA_DATA_IN, 0x81], ..usb_config(tree) });
    assert!(fake.deliver(LUMA_ULTRA_DATA_IN, &[7; 512]));
    let r = t.read(&mut buffer, ms(100));
    assert!(r.status == IoStatus::Ok && r.bytes == 512 && r.endpoint == LUMA_ULTRA_DATA_IN, "{r:?}");
    assert!(fake.deliver(0x81, &[8; 1024])); // Keeps its own, larger size.
    let r = t.read(&mut buffer, ms(100));
    assert!(r.status == IoStatus::Ok && r.bytes == 1024, "{r:?}");
}

#[test]
fn a_disconnect_records_what_led_up_to_it() {
    let _serial = serial();
    let (fake, mut t, mut buffer) = open_usb(usb_config);
    assert!(fake.deliver(0x81, &[1]));
    t.read(&mut buffer, ms(100));
    thread::sleep(ms(50));
    fake.begin_disconnect();
    fake.flush(-libc::EILSEQ); // A dying device often shows a transaction error before the flush.
    fake.remove_nodes();
    read_until(&mut t, &mut buffer, IoStatus::Disconnected, ms(500));
    t.next_event(); // Connected
    let e = t.next_event().expect("disconnected");
    assert_eq!(e.kind, EventKind::Disconnected);
    assert_eq!(e.last_urb_status, -libc::EILSEQ);
    assert!((50..2000).contains(&e.ms_since_last_packet), "{e:?}");
    // The first cause, not the ENODEV that ends the reap.
    assert!(e.reason.contains(&Errno(libc::EILSEQ).message()), "{}", e.reason);
    assert!(e.reason.contains("0x81"), "{}", e.reason);
    assert!(t.stats().urb_errors >= 1);
}

#[test]
fn read_path_allocates_nothing() {
    let _serial = serial();
    let (fake, mut t, mut buffer) = open_usb(usb_config);
    fake.stop_recording();
    let packet = [1u8; 64];
    let mut allocated = 0;
    let mut packets = 0;
    for _ in 0..5000 {
        fake.deliver(0x81, &packet);
        let before = allocations();
        let r = t.read(&mut buffer, ms(100));
        allocated += allocations() - before;
        packets += usize::from(r.status == IoStatus::Ok);
    }
    assert_eq!(packets, 5000);
    assert_eq!(allocated, 0);
}

/// The configuration nobody has to think about is the Ultra's own data channel.
#[test]
fn default_config_is_the_ultras_bulk_channel() {
    let _serial = serial();
    let (fake, mut t) =
        connected_usb(|tree| TransportConfig { sysfs_root: tree.sysfs(), dev_root: tree.dev(), ..TransportConfig::default() });
    assert!(t.open());
    assert_eq!(fake.inflight(), 8);
    assert_eq!(TransportConfig::default().arrival_settle, ms(2000)); // protocol.md's starting value, for a soak to tune.
    for urb in fake.seen() {
        assert_eq!(urb.endpoint, LUMA_ULTRA_DATA_IN);
        assert_eq!(urb.kind, usbdevfs::URB_TYPE_BULK);
        assert_eq!(urb.length, 512);
        assert_eq!(urb.flags, 0);
    }
    let mut buffer = [0u8; 2048];
    t.read(&mut buffer, ms(100));
    assert!(fake.deliver(LUMA_ULTRA_DATA_IN, &[3; 512]));
    let r = t.read(&mut buffer, ms(100));
    assert!(r.status == IoStatus::Ok && r.bytes == 512 && r.endpoint == LUMA_ULTRA_DATA_IN, "{r:?}");

    assert_eq!(t.write(&[0xff; 512], WRITE), IoStatus::Ok);
    let last = *fake.seen().last().expect("a write URB");
    assert!(last.endpoint == LUMA_ULTRA_DATA_OUT && last.kind == usbdevfs::URB_TYPE_BULK && last.length == 512);
    assert_eq!(last.flags, 0); // No zero-length packet after a full-size frame: frames are one packet each.
}

/// An interrupt endpoint remains possible for the stream: which one it is awaits a capture.
#[test]
fn interrupt_endpoints_get_interrupt_urbs() {
    let _serial = serial();
    let (fake, mut t) = connected_usb(usb_config);
    t.open();
    for urb in fake.seen() {
        let want = if urb.endpoint == 0x81 { usbdevfs::URB_TYPE_INTERRUPT } else { usbdevfs::URB_TYPE_BULK };
        assert_eq!(urb.kind, want);
    }
}

#[test]
fn usbfs_claims_only_the_needed_interface_every_time() {
    let _serial = serial();
    let (fake, mut t, mut buffer) = open_usb(|tree| TransportConfig { interface_number: 0, ..usb_config(tree) });
    fake.yank();
    read_until(&mut t, &mut buffer, IoStatus::Disconnected, ms(500));
    fake.arrive(102);
    read_until(&mut t, &mut buffer, IoStatus::Connected, ms(500));
    assert_eq!(fake.claimed_list(), vec![0, 0]);
}

/// The queue is what keeps the glasses from backing up: every read must leave it full.
#[test]
fn usbfs_reads_stay_queued_through_a_burst() {
    let _serial = serial();
    let (fake, mut t, mut buffer) = open_usb(|tree| TransportConfig {
        in_endpoints: vec![LUMA_ULTRA_DATA_IN],
        urbs_per_endpoint: 8,
        ..usb_config(tree)
    });
    for _ in 0..50 {
        for i in 0..8u8 {
            assert!(fake.deliver(LUMA_ULTRA_DATA_IN, &[i; 512]));
        }
        assert_eq!(fake.inflight(), 0); // The whole queue has been used: the worst moment.
        for i in 0..8u8 {
            let r = t.read(&mut buffer, ms(100));
            assert!(r.status == IoStatus::Ok && buffer[0] == i, "{r:?}");
            assert_eq!(fake.inflight(), 8); // Back to full from the first read on.
        }
    }
    assert_eq!(fake.missed(), 0);
}

/// Only teardown cancels our reads, and it reaps its own, so this should not happen on a real kernel.
/// If it ever does, the queue is short and carrying on would leave a link that reads nothing.
#[test]
fn usbfs_a_read_cancelled_by_the_kernel_is_a_lost_link() {
    let _serial = serial();
    for status in [-libc::ENOENT, -libc::ECONNRESET] {
        let (fake, mut t, mut buffer) = open_usb(usb_config);
        assert!(fake.deliver_status(0x81, &[], status));
        assert_eq!(read_until(&mut t, &mut buffer, IoStatus::Disconnected, ms(300)).status, IoStatus::Disconnected);
        assert_eq!(read_until(&mut t, &mut buffer, IoStatus::Connected, ms(500)).status, IoStatus::Connected);
        assert_eq!(fake.inflight(), 4); // A full queue again, not a link that looks open and reads nothing.
    }
}

/// Reconnecting straight after a drop has been followed by another drop (usb-drop-fix: 12 of 20,
/// against 0 of 160 fresh starts). A settle holds the open back, and the wait restarts if the device
/// vanishes again during it.
#[test]
fn arrival_settle_holds_the_reopen_back() {
    let _serial = serial();
    let (fake, mut t) = connected_usb(|tree| TransportConfig { arrival_settle: ms(120), ..usb_config(tree) });
    let start = Instant::now();
    assert!(t.open()); // A first open has nothing to settle from.
    assert!(ms_since(start) < 100);
    let mut buffer = [0u8; 2048];
    t.read(&mut buffer, ms(100));
    assert!(fake.deliver(0x81, &[1])); // A good session: only the plain settle applies.
    assert_eq!(t.read(&mut buffer, ms(100)).status, IoStatus::Ok);
    fake.yank();
    read_until(&mut t, &mut buffer, IoStatus::Disconnected, ms(500));
    let opens = fake.opens();

    fake.arrive(102);
    t.read(&mut buffer, ms(70));
    assert_eq!(fake.opens(), opens);
    fake.yank(); // Gone again, part way through.
    t.read(&mut buffer, ms(30));
    fake.arrive(103);
    let start = Instant::now();
    assert_eq!(read_until(&mut t, &mut buffer, IoStatus::Connected, ms(1000)).status, IoStatus::Connected);
    assert!(ms_since(start) >= 100); // The full settle again, not what was left of the first one.
    assert_eq!(fake.opens(), opens + 1); // Then opened once.
}

/// While something else holds the glasses, open and claim must not run at the rescan rate.
#[test]
fn failed_opens_back_off_but_finding_the_device_stays_fast() {
    let _serial = serial();
    let (fake, mut t) = connected_usb(|tree| TransportConfig { open_retry_max: ms(1000), ..usb_config(tree) });
    fake.set_busy(true);
    t.open();
    let mut buffer = [0u8; 2048];
    let start = Instant::now();
    while ms_since(start) < 300 {
        t.read(&mut buffer, ms(20));
    }
    assert!(fake.opens() <= 7, "{}", fake.opens()); // 10 ms apart it would be 30.
    assert!(fake.opens() >= 3, "{}", fake.opens());
    fake.set_busy(false);
    assert_eq!(read_until(&mut t, &mut buffer, IoStatus::Connected, ms(1500)).status, IoStatus::Connected);

    // A device that is simply absent is still looked for at full rate, however long it has been.
    fake.yank();
    read_until(&mut t, &mut buffer, IoStatus::Disconnected, ms(500));
    let start = Instant::now();
    while ms_since(start) < 100 {
        t.read(&mut buffer, ms(20));
    }
    fake.arrive(150);
    let start = Instant::now();
    assert_eq!(read_until(&mut t, &mut buffer, IoStatus::Connected, ms(500)).status, IoStatus::Connected);
    assert!(ms_since(start) < 60);
}

/// Incident 20261104T051931Z: after a cold boot the node had no user ACL, xrDriver's SDK failed to open
/// it, went on to use the handle anyway and crashed. Here a failed open must end all contact with the
/// device, say why in a way a program can read, and recover once access appears.
#[test]
fn no_permission_never_touches_the_device_and_says_why() {
    let _serial = serial();
    let (fake, mut t) = connected_usb(usb_config);
    fake.fail_opens(1_000_000, libc::EACCES);
    assert!(!t.open());
    let mut buffer = [0u8; 2048];
    assert_eq!(t.read(&mut buffer, ms(80)).status, IoStatus::Timeout);
    assert!(!t.connected());
    assert_eq!(t.stats().last_open_errno, libc::EACCES);
    assert!(t.last_error().contains("Permission denied"), "{}", t.last_error());
    assert!(t.last_error().contains("replug"), "{}", t.last_error()); // Tells a person what to do.
    assert_eq!(t.write(&[1; 512], WRITE), IoStatus::Disconnected);
    assert!(fake.requests().is_empty()); // Not one ioctl: no claim, no URB, nothing on a handle that never opened.
    assert_eq!(t.stats().connects, 0);

    fake.fail_opens(0, 0); // Replugged, or the rule installed.
    assert_eq!(read_until(&mut t, &mut buffer, IoStatus::Connected, ms(1500)).status, IoStatus::Connected);
    assert_eq!(t.stats().last_open_errno, 0);
    assert!(t.last_error().is_empty(), "{}", t.last_error());
}

#[test]
fn busy_interface_reports_its_errno() {
    let _serial = serial();
    let (fake, mut t) = connected_usb(usb_config);
    fake.set_busy(true);
    assert!(!t.open());
    assert_eq!(t.stats().last_open_errno, libc::EBUSY);
}

/// A slow sysfs scan could leave the next scan in the past. A negative poll timeout waits for ever, and
/// read() would then stop rescanning, so the glasses would never come back.
#[test]
fn a_slow_scan_never_makes_poll_wait_for_ever() {
    struct Watch {
        lowest: AtomicI32,
    }
    impl Sys for Watch {
        fn open(&self, path: &Path, flags: c_int) -> Result<RawFd, Errno> {
            LibcSys.open(path, flags)
        }
        fn close(&self, fd: RawFd) {
            LibcSys.close(fd)
        }
        fn poll(&self, fds: &mut [libc::pollfd], timeout_ms: c_int) -> Result<usize, Errno> {
            self.lowest.fetch_min(timeout_ms, Ordering::Relaxed);
            LibcSys.poll(fds, timeout_ms.max(0))
        }
        unsafe fn ioctl(&self, fd: RawFd, request: Request<'_>) -> Result<(), Errno> {
            unsafe { LibcSys.ioctl(fd, request) }
        }
    }
    let _serial = serial();
    let tree = FakeTree::new();
    for i in 0..4000 {
        // Enough entries that one scan outlasts the rescan interval.
        let d = tree.sysfs().join(format!("bus/usb/devices/9-{i}"));
        write_file(&d.join("idVendor"), "1234");
        write_file(&d.join("idProduct"), "5678");
    }
    let watch = Arc::new(Watch { lowest: AtomicI32::new(0) });
    let mut t = Transport::with_sys(TransportConfig { rescan_interval: ms(1), ..usb_config(&tree) }, watch.clone());
    t.open();
    let mut buffer = [0u8; 2048];
    let start = Instant::now();
    while ms_since(start) < 120 {
        assert_eq!(t.read(&mut buffer, ms(30)).status, IoStatus::Timeout);
    }
    assert!(watch.lowest.load(Ordering::Relaxed) >= 0);
}

/// The moment the glasses are backed up is the moment the ring holds packets. Handing those over must
/// not leave the kernel's queue empty.
#[test]
fn usbfs_reads_refill_while_the_ring_drains() {
    let _serial = serial();
    let (fake, mut t, mut buffer) = open_usb(|tree| TransportConfig {
        in_endpoints: vec![LUMA_ULTRA_DATA_IN],
        urbs_per_endpoint: 8,
        ..usb_config(tree)
    });
    let mut next = 0u8;
    let mut expected = 0u8;
    let mut send = |n: usize| {
        for _ in 0..n {
            assert!(fake.deliver(LUMA_ULTRA_DATA_IN, &[next; 512]));
            next += 1;
        }
    };
    send(8);
    let r = t.read(&mut buffer, ms(100)); // Takes all 8 from the kernel, hands over one.
    assert!(r.status == IoStatus::Ok && buffer[0] == expected, "{r:?}");
    expected += 1;
    send(8); // The glasses keep going while the caller works through the other 7.
    assert_eq!(fake.inflight(), 0);
    let r = t.read(&mut buffer, ms(100));
    assert!(r.status == IoStatus::Ok && buffer[0] == expected, "{r:?}");
    expected += 1;
    assert_eq!(fake.inflight(), 8); // Refilled by a read that came from the ring.
    while expected < 16 {
        let r = t.read(&mut buffer, ms(100));
        assert!(r.status == IoStatus::Ok && buffer[0] == expected, "{r:?}");
        expected += 1;
    }
    assert_eq!(t.stats().dropped_packets, 0);
}

/// One fault fails the whole queue at once. It is one failure: close, once, and wait.
#[test]
fn usbfs_a_failed_read_closes_everything_once() {
    let _serial = serial();
    let (fake, mut t, mut buffer) = open_usb(|tree| TransportConfig {
        in_endpoints: vec![LUMA_ULTRA_DATA_IN],
        urbs_per_endpoint: 8,
        arrival_settle: ms(80),
        ..usb_config(tree)
    });
    assert!(fake.deliver(LUMA_ULTRA_DATA_IN, &[1]));
    assert_eq!(t.read(&mut buffer, ms(100)).status, IoStatus::Ok);

    fake.fail_all(LUMA_ULTRA_DATA_IN, -libc::EPROTO);
    assert_eq!(t.read(&mut buffer, ms(100)).status, IoStatus::Disconnected);
    assert!(!t.connected());
    assert_eq!(fake.inflight(), 0); // Nothing is left polling a sick link.
    assert!(!fake.descriptor_open());
    assert_eq!(fake.claims(), 1);
    assert_eq!(t.read(&mut buffer, ms(30)).status, IoStatus::Timeout); // Reported once, and held off.
    assert_eq!(fake.claims(), 1);
    assert_eq!(read_until(&mut t, &mut buffer, IoStatus::Connected, ms(500)).status, IoStatus::Connected);
    assert_eq!(fake.inflight(), 8);
    assert_eq!(t.stats().disconnects, 1);
    t.next_event();
    let e = t.next_event().expect("disconnected");
    assert!(e.reason.contains("0x87"), "{}", e.reason);
}

/// Regression: valid IMU traffic followed by MCU EPROTO must not erase the cooldown. The C++ test
/// runs this through TransportLink; here the transport's own read loop holds the reopen back.
fn check_short_error_sessions(healthy_after: Option<Duration>) {
    let _serial = serial();
    let (fake, mut t) = connected_usb(|tree| {
        let mut config =
            TransportConfig { urbs_per_endpoint: 8, arrival_settle: ms(20), ..live_config(tree) };
        if let Some(healthy) = healthy_after {
            config.healthy_after = healthy;
        }
        config
    });
    let healthy_after = healthy_after.unwrap_or(TransportConfig::default().healthy_after);
    assert!(t.open()); // First open is immediate.
    let mut buffer = [0u8; 2048];
    assert_eq!(t.read(&mut buffer, ms(50)).status, IoStatus::Connected);

    let packet = |t: &mut Transport, buffer: &mut Buffer| {
        assert!(fake.deliver(0x81, &IMU));
        assert_eq!(t.read(buffer, ms(50)).status, IoStatus::Ok);
    };
    let fail_and_recover = |t: &mut Transport, buffer: &mut Buffer, hold: Duration, error: i32| {
        assert!(fake.deliver_status(0x85, &[], error));
        assert_eq!(t.read(buffer, ms(50)).status, IoStatus::Disconnected);
        let failed = Instant::now();
        let (opens, claims, submits) = (fake.opens(), fake.claims(), fake.submitted_endpoints().len());
        while failed.elapsed() + ms(15) < hold {
            let left = hold - ms(15) - failed.elapsed();
            assert_eq!(t.read(buffer, left.min(ms(20))).status, IoStatus::Timeout, "hold {hold:?}");
        }
        assert_eq!(fake.opens(), opens);
        assert_eq!(fake.claims(), claims);
        assert_eq!(fake.submitted_endpoints().len(), submits);
        assert_eq!(fake.inflight(), 0);
        // A finite upper bound also catches an unbounded counter, multiplier or quarantine.
        assert_eq!(read_until(t, buffer, IoStatus::Connected, ms(200)).status, IoStatus::Connected, "hold {hold:?}");
        assert_eq!(fake.opens(), opens + 1);
        assert_eq!(fake.claims(), claims + 1);
        assert_eq!(fake.inflight(), 16);
    };
    for hold in [40, 80, 160, 320, 320, 320] {
        packet(&mut t, &mut buffer);
        fail_and_recover(&mut t, &mut buffer, ms(hold), -libc::EPROTO);
    }

    // A long idle gap between two payloads is not sustained healthy traffic.
    packet(&mut t, &mut buffer);
    assert_eq!(t.read(&mut buffer, healthy_after + ms(150)).status, IoStatus::Timeout);
    packet(&mut t, &mut buffer);
    fail_and_recover(&mut t, &mut buffer, ms(320), -libc::EPROTO);

    // Continuous traffic for the configured duration clears the accumulated penalty.
    let start = Instant::now();
    loop {
        packet(&mut t, &mut buffer);
        assert_eq!(t.read(&mut buffer, ms(20)).status, IoStatus::Timeout);
        if start.elapsed() >= healthy_after + ms(100) {
            break;
        }
    }
    fail_and_recover(&mut t, &mut buffer, ms(20), -libc::EPROTO);
    packet(&mut t, &mut buffer);
    fail_and_recover(&mut t, &mut buffer, ms(40), -libc::EPIPE); // Same-device retry remains allowed; no clear-halt.

    // Stop still interrupts a long cooldown without opening, claiming or queuing anything.
    packet(&mut t, &mut buffer);
    assert!(fake.deliver_status(0x85, &[], -libc::EPROTO));
    assert_eq!(t.read(&mut buffer, ms(50)).status, IoStatus::Disconnected);
    let opens = fake.opens();
    t.interrupt();
    let start = Instant::now();
    assert_eq!(t.read(&mut buffer, ms(5000)).status, IoStatus::Interrupted);
    assert!(ms_since(start) < 100);
    assert_eq!(fake.opens(), opens);

    // Observed disappearance clears the penalty; a fresh arrival gets only the base hold.
    fake.yank();
    assert_eq!(t.read(&mut buffer, ms(30)).status, IoStatus::Timeout);
    fake.arrive(150);
    let start = Instant::now();
    assert_eq!(read_until(&mut t, &mut buffer, IoStatus::Connected, ms(100)).status, IoStatus::Connected);
    assert!(ms_since(start) < 70);
    assert_eq!(fake.inflight(), 16);
    for request in fake.requests() {
        assert!(usbdevfs::is_allowed_ioctl(request));
    }
}

#[test]
fn short_error_sessions_keep_backoff() {
    check_short_error_sessions(None);
}

#[test]
fn configurable_healthy_interval_clears_backoff() {
    check_short_error_sessions(Some(ms(60)));
}

/// Nothing here may clear a halt, so a reopen cannot fix one. Without a limit that is a reconnect storm:
/// 79 connects in 2 s, measured against an earlier version. Only a new enumeration heals it.
#[test]
fn a_stall_cannot_become_a_reopen_storm() {
    let _serial = serial();
    let (fake, mut t, mut buffer) = open_usb(|tree| TransportConfig {
        in_endpoints: vec![LUMA_ULTRA_DATA_IN],
        arrival_settle: ms(20),
        open_retry_max: ms(30),
        ..usb_config(tree)
    });
    fake.stall(LUMA_ULTRA_DATA_IN);
    let start = Instant::now();
    let mut connects = 0;
    while ms_since(start) < 500 {
        connects += usize::from(t.read(&mut buffer, ms(20)).status == IoStatus::Connected);
    }
    assert!(connects >= 2, "{connects}"); // It does keep trying, in case something else cleared it.
    assert!(connects <= 5, "{connects}"); // Holds of 40, 80, 160, 320 ms, not one every 20 ms.
    assert!(t.last_error().contains("halted"), "{}", t.last_error());

    fake.yank(); // The glasses leave and come back: that clears the halt, and the penalty with it.
    read_until(&mut t, &mut buffer, IoStatus::Disconnected, ms(1000));
    t.read(&mut buffer, ms(30));
    fake.arrive(150);
    let start = Instant::now();
    assert_eq!(read_until(&mut t, &mut buffer, IoStatus::Connected, ms(1000)).status, IoStatus::Connected);
    assert!(ms_since(start) < 200);
    assert!(fake.deliver(LUMA_ULTRA_DATA_IN, &[1]));
    assert_eq!(t.read(&mut buffer, ms(100)).status, IoStatus::Ok);
}

/// Another program's reset raises no poll bit and keeps the device number, and takes our claim away.
#[test]
fn a_foreign_reset_is_noticed_and_the_interface_reclaimed() {
    let _serial = serial();
    let (fake, mut t, mut buffer) = open_usb(usb_config);
    fake.foreign_reset();
    assert_eq!(read_until(&mut t, &mut buffer, IoStatus::Disconnected, ms(300)).status, IoStatus::Disconnected);
    assert_eq!(read_until(&mut t, &mut buffer, IoStatus::Connected, ms(500)).status, IoStatus::Connected);
    assert_eq!(fake.claims(), 2);
    assert_eq!(fake.inflight(), 4);
    assert_eq!(fake.auto_claims(), 0); // Never submitted on a claim the kernel had dropped.
}

/// The kernel's real order: hung up first, URBs still pending, node still there for a while.
#[test]
fn a_disconnect_in_real_order() {
    let _serial = serial();
    let (fake, mut t, mut buffer) = open_usb(usb_config);
    let stages = {
        let fake = fake.clone();
        thread::spawn(move || {
            thread::sleep(ms(20));
            fake.begin_disconnect();
        })
    };
    let start = Instant::now();
    let r = t.read(&mut buffer, ms(5000));
    stages.join().unwrap();
    assert_eq!(r.status, IoStatus::Disconnected); // On the hang-up alone, nothing has completed yet.
    assert!(ms_since(start) < 500);
    assert_eq!(t.read(&mut buffer, ms(40)).status, IoStatus::Timeout); // The dying node is still there and will not open.
    fake.flush(-libc::ESHUTDOWN);
    fake.remove_nodes();
    fake.arrive(102);
    assert_eq!(read_until(&mut t, &mut buffer, IoStatus::Connected, ms(1000)).status, IoStatus::Connected);
    assert_eq!(t.stats().disconnects, 1); // One drop, reported once.
    assert_eq!(fake.inflight(), 4);
}

#[test]
fn default_settle_holds_a_reconnect_after_a_drop() {
    let _serial = serial();
    let (fake, mut t) =
        connected_usb(|tree| TransportConfig { sysfs_root: tree.sysfs(), dev_root: tree.dev(), ..TransportConfig::default() });
    assert!(t.open());
    let mut buffer = [0u8; 2048];
    t.read(&mut buffer, ms(100));
    fake.yank();
    read_until(&mut t, &mut buffer, IoStatus::Disconnected, ms(500));
    let opens = fake.opens();
    t.read(&mut buffer, ms(60));
    fake.arrive(102);
    t.read(&mut buffer, ms(200));
    assert_eq!(fake.opens(), opens); // Back, and left alone.
}

#[test]
fn a_failed_write_closes_the_link() {
    let _serial = serial();
    let (fake, mut t, mut buffer) = open_usb(usb_config);
    fake.out_status(-libc::EPIPE);
    assert_eq!(t.write(&[1], WRITE), IoStatus::Disconnected);
    assert!(!t.connected());
    assert_eq!(read_until(&mut t, &mut buffer, IoStatus::Disconnected, ms(300)).status, IoStatus::Disconnected);
    t.next_event();
    let e = t.next_event().expect("disconnected");
    assert_eq!(e.last_urb_status, -libc::EPIPE);
    assert!(e.reason.contains("0x06"), "{}", e.reason);
}

/// A write that finished just as the timeout fired went out. Saying otherwise invites sending it twice.
#[test]
fn a_write_finishing_as_it_times_out_reports_the_truth() {
    let _serial = serial();
    let (fake, mut t) = connected_usb(usb_config);
    t.open();
    fake.hold_out_urbs(true);
    fake.finish_held_urb_on_discard();
    assert_eq!(t.write(&[1; 4], ms(10)), IoStatus::Ok);
    assert!(t.connected());
}

#[test]
fn a_write_with_no_timeout_still_gets_one_look() {
    let _serial = serial();
    let (_fake, mut t) = connected_usb(usb_config);
    t.open();
    assert_eq!(t.write(&[1], ms(0)), IoStatus::Ok);
}

/// A packet larger than the URB is babble on a real host, not a shorter read, so refuse it up front.
#[test]
fn a_packet_size_below_the_endpoints_is_refused() {
    let _serial = serial();
    let (fake, mut t) = connected_usb(|tree| TransportConfig { packet_size: 64, ..usb_config(tree) });
    assert!(!t.open());
    assert!(t.last_error().contains("packet_size"), "{}", t.last_error());
    assert_eq!(fake.claims(), 0);
    assert!(!fake.descriptor_open());
}

#[test]
fn open_while_down_leaves_reconnecting_to_read() {
    let _serial = serial();
    let (fake, mut t, mut buffer) = open_usb(usb_config);
    fake.yank();
    assert_eq!(t.write(&[1], WRITE), IoStatus::Disconnected);
    fake.arrive(102);
    assert!(!t.open()); // read() has not heard of the drop yet.
    assert_eq!(t.read(&mut buffer, ms(100)).status, IoStatus::Disconnected);
    assert_eq!(read_until(&mut t, &mut buffer, IoStatus::Connected, ms(500)).status, IoStatus::Connected);
}

#[test]
fn at_least_two_reads_stay_queued() {
    let _serial = serial();
    let (fake, mut t) =
        connected_usb(|tree| TransportConfig { in_endpoints: vec![0x81], urbs_per_endpoint: 1, ..usb_config(tree) });
    t.open();
    assert_eq!(fake.inflight(), 2);
}

#[test]
fn usbfs_close_cancels_and_releases() {
    let _serial = serial();
    let (fake, mut t) = connected_usb(usb_config);
    t.open();
    t.close();
    assert_eq!(fake.request_count(usbdevfs::DISCARDURB), 4);
    assert_eq!(fake.inflight(), 0);
    assert_eq!(fake.releases(), 1);
    assert!(!fake.descriptor_open());
}

// ---------------------------------------------------------------- never reset

#[test]
fn a_whole_session_only_used_allowed_ioctls() {
    let _serial = serial();
    let (fake, mut t) = connected_usb(usb_config);
    {
        t.open();
        let mut buffer = [0u8; 2048];
        t.read(&mut buffer, ms(50));
        fake.deliver(0x81, &[1]);
        t.read(&mut buffer, ms(50));
        t.write(&[1], WRITE);
        fake.stall(0x81); // A halted endpoint: close, never clear the halt.
        read_until(&mut t, &mut buffer, IoStatus::Disconnected, ms(500));
        read_until(&mut t, &mut buffer, IoStatus::Connected, ms(500));
        fake.yank();
        read_until(&mut t, &mut buffer, IoStatus::Disconnected, ms(500));
        fake.arrive(103);
        read_until(&mut t, &mut buffer, IoStatus::Connected, ms(500));
        drop(t);
    }
    let used = fake.requests();
    assert!(used.contains(&usbdevfs::SUBMITURB));
    for request in used {
        assert!(usbdevfs::is_allowed_ioctl(request), "0x{request:x}");
    }
}

fn golden_lines() -> Vec<String> {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../corpus/luma/transport_trace.txt");
    let text = std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("{}: {e}", path.display()));
    text.lines().filter(|l| !l.is_empty() && !l.starts_with('#')).map(str::to_string).collect()
}

fn request_table() -> Vec<String> {
    usbdevfs::REQUESTS
        .iter()
        .map(|&(name, code)| {
            let verdict = if usbdevfs::is_allowed_ioctl(code) { "allowed" } else { "refused" };
            format!("request {name} 0x{code:x} {verdict}")
        })
        .collect()
}

/// The request codes and the allowlist are the ones the C++ driver reads from the kernel header.
#[test]
fn the_request_table_matches_the_kernel_header() {
    let golden: Vec<String> = golden_lines().into_iter().filter(|l| l.starts_with("request ")).collect();
    assert_eq!(request_table(), golden);
}

/// The script of the C++ test of the same name, single-threaded so the order of calls is fixed: connect,
/// read, write, a write timeout, a halt, a yank and a return. Both drivers must make exactly the calls in
/// rust/corpus/luma/transport_trace.txt, in that order: the fake kernel's stand-in for an strace diff.
#[test]
fn a_scripted_session_matches_the_shared_trace() {
    let _serial = serial();
    let (fake, mut t) = connected_usb(usb_config);
    fake.start_trace();
    {
        let mut buffer = [0u8; 2048];
        t.open();
        assert_eq!(t.read(&mut buffer, ms(50)).status, IoStatus::Connected);
        fake.deliver(0x81, &[1]);
        assert_eq!(t.read(&mut buffer, ms(50)).status, IoStatus::Ok);
        fake.deliver(0x82, &[2, 3]);
        assert_eq!(t.read(&mut buffer, ms(50)).status, IoStatus::Ok);
        assert_eq!(t.write(&[1], WRITE), IoStatus::Ok);
        fake.hold_out_urbs(true);
        assert_eq!(t.write(&[2], ms(20)), IoStatus::Timeout);
        fake.hold_out_urbs(false);
        fake.stall(0x81);
        assert_eq!(read_until(&mut t, &mut buffer, IoStatus::Disconnected, ms(500)).status, IoStatus::Disconnected);
        assert_eq!(read_until(&mut t, &mut buffer, IoStatus::Connected, ms(500)).status, IoStatus::Connected);
        // Still halted: reopening does not clear it.
        assert_eq!(t.read(&mut buffer, ms(50)).status, IoStatus::Disconnected);
        fake.yank();
        fake.arrive(103);
        assert_eq!(read_until(&mut t, &mut buffer, IoStatus::Connected, ms(500)).status, IoStatus::Connected);
        fake.deliver(0x82, &[4]);
        assert_eq!(t.read(&mut buffer, ms(50)).status, IoStatus::Ok);
        t.close();
        drop(t);
    }
    let mut lines = request_table();
    lines.extend(fake.trace());
    let golden = golden_lines();
    for (i, (got, want)) in lines.iter().zip(&golden).enumerate() {
        assert_eq!(got, want, "trace line {}", i + 1);
    }
    assert_eq!(lines.len(), golden.len(), "trace length");
}
