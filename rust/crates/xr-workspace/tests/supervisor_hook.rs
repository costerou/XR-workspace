//! The run loop's hook (`supervisor::hook`): what the overview pointer's `poll` gets from the loop. Frames handed to
//! the hook reach the renderer socket, a signal on its eventfd wakes the loop at once (not at the next 50 ms pass),
//! its deadline is kept, and an idle hook adds no loop pass at all. The Python counterpart is
//! `test_free_pointer.WiringTests.test_the_supervisor_loop_wakes_on_a_sample_and_sends_the_dot`.

use std::io::Read;
use std::os::unix::net::UnixStream;
use std::os::unix::process::ExitStatusExt;
use std::process::ExitStatus;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use xr_workspace::supervisor::{LoopHook, POLL_INTERVAL, Renderer, StopReason, Supervisor, WakeFd};

struct Up(Option<ExitStatus>);

impl Renderer for Up {
    fn poll(&mut self) -> Option<ExitStatus> {
        self.0
    }
    fn terminate(&mut self) {
        self.0 = Some(ExitStatus::from_raw(0));
    }
    fn kill(&mut self) {
        self.0 = Some(ExitStatus::from_raw(9));
    }
}

fn watchdog() -> Supervisor<Up, &'static str> {
    Supervisor::new(Up(None), |_| "done", Instant::now())
}

/// What a producer thread (the sampler) shares with the hook.
#[derive(Default)]
struct Shared {
    queued: Mutex<Vec<u8>>,
    polls: Mutex<Vec<Instant>>,
    deadline: Mutex<Option<Instant>>,
}

struct Hook {
    wake: Arc<WakeFd>,
    shared: Arc<Shared>,
}

impl LoopHook for Hook {
    fn wake_fd(&self) -> &WakeFd {
        &self.wake
    }
    fn deadline(&mut self) -> Option<Instant> {
        *self.shared.deadline.lock().unwrap()
    }
    fn poll(&mut self, now: Instant, out: &mut Vec<u8>) {
        self.shared.polls.lock().unwrap().push(now);
        let mut deadline = self.shared.deadline.lock().unwrap();
        if deadline.is_some_and(|d| now >= d) {
            *deadline = None;
        }
        out.append(&mut self.shared.queued.lock().unwrap());
    }
}

fn hook() -> (Hook, Arc<WakeFd>, Arc<Shared>) {
    let wake = Arc::new(WakeFd::new().unwrap());
    let shared = Arc::new(Shared::default());
    (
        Hook {
            wake: Arc::clone(&wake),
            shared: Arc::clone(&shared),
        },
        wake,
        shared,
    )
}

#[test]
fn frames_handed_to_the_hook_reach_the_renderer() {
    let (parent, mut peer) = UnixStream::pair().unwrap();
    let (mut hook, wake, shared) = hook();
    let interrupt = Arc::new(AtomicBool::new(false));
    let stop = Arc::clone(&interrupt);
    let producer = thread::spawn(move || {
        let frames: &[&[u8]] = &[
            b"{\"op\":\"overview_pointer\",\"payload\":{\"x\":1}}\n",
            b"{\"op\":\"overview_pointer\",\"payload\":{\"x\":2}}\n",
        ];
        let mut got = Vec::new();
        for frame in frames {
            shared.queued.lock().unwrap().extend_from_slice(frame);
            wake.signal();
            let mut buf = vec![0u8; frame.len()];
            peer.read_exact(&mut buf).unwrap();
            got.extend_from_slice(&buf);
        }
        stop.store(true, Ordering::SeqCst);
        // The renderer's end stays open until the loop has seen the interrupt.
        (got, frames.concat(), wake, peer)
    });
    let mut w = watchdog();
    assert_eq!(
        w.run_hosting(&parent, None, &mut hook, &interrupt, POLL_INTERVAL)
            .unwrap(),
        "done"
    );
    let (got, want, wake, _peer) = producer.join().unwrap();
    assert_eq!(got, want);
    assert_eq!(w.stop_reason, Some(StopReason::Interrupted));
    assert!(!wake.drain(), "the loop drained the wake fd");
}

#[test]
fn a_signal_wakes_the_loop_at_once() {
    // A 100 ms pass: a wake that waited for the next pass would take 50 ms on average.
    let interval = Duration::from_millis(100);
    let (parent, _peer) = UnixStream::pair().unwrap();
    let (mut hook, wake, shared) = hook();
    let interrupt = Arc::new(AtomicBool::new(false));
    let stop = Arc::clone(&interrupt);
    let observed = Arc::clone(&shared);
    let producer = thread::spawn(move || {
        let mut latencies = Vec::new();
        for _ in 0..10 {
            thread::sleep(Duration::from_millis(37));
            let before = observed.polls.lock().unwrap().len();
            let at = Instant::now();
            wake.signal();
            let deadline = at + Duration::from_secs(1);
            while observed.polls.lock().unwrap().len() == before && Instant::now() < deadline {
                thread::sleep(Duration::from_micros(200));
            }
            let polls = observed.polls.lock().unwrap();
            assert!(polls.len() > before, "the signal was never polled");
            latencies.push(polls[before].duration_since(at));
        }
        stop.store(true, Ordering::SeqCst);
        latencies
    });
    let mut w = watchdog();
    w.run_hosting(&parent, None, &mut hook, &interrupt, interval)
        .unwrap();
    let mut latencies = producer.join().unwrap();
    latencies.sort();
    assert!(
        latencies[latencies.len() / 2] < Duration::from_millis(10),
        "median wake to poll {:?}",
        latencies
    );
    assert_eq!(
        shared.polls.lock().unwrap().len(),
        10,
        "one poll per signal"
    );
}

#[test]
fn the_hook_is_polled_at_its_deadline() {
    let (parent, _peer) = UnixStream::pair().unwrap();
    let (mut hook, _wake, shared) = hook();
    let start = Instant::now();
    let due = start + Duration::from_millis(30);
    *shared.deadline.lock().unwrap() = Some(due);
    let interrupt = Arc::new(AtomicBool::new(false));
    let stop = Arc::clone(&interrupt);
    let t = thread::spawn(move || {
        thread::sleep(Duration::from_millis(300));
        stop.store(true, Ordering::SeqCst);
    });
    let mut w = watchdog();
    w.run_hosting(&parent, None, &mut hook, &interrupt, POLL_INTERVAL)
        .unwrap();
    t.join().unwrap();
    let polls = shared.polls.lock().unwrap();
    assert_eq!(
        polls.len(),
        1,
        "polled once, at its deadline, and not again"
    );
    assert!(
        polls[0] >= due && polls[0] - due < Duration::from_millis(20),
        "{:?}",
        polls[0] - due
    );
}

#[test]
fn an_idle_hook_adds_no_loop_pass() {
    let (parent, _peer) = UnixStream::pair().unwrap();
    let (mut hook, _wake, shared) = hook();
    let interrupt = Arc::new(AtomicBool::new(false));
    let stop = Arc::clone(&interrupt);
    let window = Duration::from_millis(1000);
    let t = thread::spawn(move || {
        thread::sleep(window);
        stop.store(true, Ordering::SeqCst);
    });
    let mut w = watchdog();
    w.run_hosting(&parent, None, &mut hook, &interrupt, POLL_INTERVAL)
        .unwrap();
    t.join().unwrap();
    // No heartbeat and no signal: only the watchdog's own 50 ms pass (20 in the window, and the one that ends it).
    let ceiling = (window.as_millis() / POLL_INTERVAL.as_millis()) as u64 + 3;
    assert!(w.loop_passes <= ceiling, "{} loop passes", w.loop_passes);
    assert!(
        shared.polls.lock().unwrap().is_empty(),
        "an idle hook is never polled"
    );
}

#[test]
fn a_stopping_run_polls_no_hook() {
    let (parent, peer) = UnixStream::pair().unwrap();
    drop(peer); // renderer-closed at once
    let (mut hook, wake, shared) = hook();
    shared.queued.lock().unwrap().extend_from_slice(b"x\n");
    *shared.deadline.lock().unwrap() = Some(Instant::now());
    wake.signal();
    let mut w = watchdog();
    w.run_hosting(
        &parent,
        None,
        &mut hook,
        &AtomicBool::new(false),
        POLL_INTERVAL,
    )
    .unwrap();
    // The first pass sees the hangup and the signal together: the disconnect comes first, so nothing is sent.
    assert_eq!(w.stop_reason, Some(StopReason::RendererClosed));
    assert!(shared.polls.lock().unwrap().is_empty());
}

/// The view controls' side of the hook (`view_controls`): a source the loop waits on (the control socket), the status
/// datagrams it is handed, and a descriptor that travels with its frame (a VR video's file).
struct Sourced {
    wake: WakeFd,
    source: UnixStream,
    file: Option<std::os::fd::OwnedFd>,
    statuses: Arc<Mutex<Vec<Vec<u8>>>>,
}

impl LoopHook for Sourced {
    fn wake_fd(&self) -> &WakeFd {
        &self.wake
    }
    fn poll(&mut self, _now: Instant, _out: &mut Vec<u8>) {}
    fn sources(&self) -> Vec<std::os::fd::RawFd> {
        vec![std::os::fd::AsRawFd::as_raw_fd(&self.source)]
    }
    fn ready(&mut self, index: usize, _now: Instant, out: &mut Vec<u8>) -> bool {
        assert_eq!(index, 0);
        let mut op = [0u8; 64];
        let n = (&self.source).read(&mut op).unwrap();
        if n == 0 {
            return false; // ended: never woken by it again
        }
        out.extend_from_slice(b"{\"op\":\"video\"}\n");
        true
    }
    fn status(&mut self, datagram: &[u8], _now: Instant) {
        self.statuses.lock().unwrap().push(datagram.to_vec());
    }
    fn take_fds(&mut self) -> Vec<std::os::fd::OwnedFd> {
        self.file.take().into_iter().collect()
    }
}

#[test]
fn a_source_its_status_and_a_frame_with_a_descriptor() {
    use std::io::Write;
    use std::os::unix::net::UnixDatagram;
    let (parent, peer) = UnixStream::pair().unwrap();
    let (status_parent, status_child) = UnixDatagram::pair().unwrap();
    let (source, mut user) = UnixStream::pair().unwrap();
    let statuses = Arc::new(Mutex::new(Vec::new()));
    let mut hook = Sourced {
        wake: WakeFd::new().unwrap(),
        source,
        file: Some(std::fs::File::open("/dev/null").unwrap().into()),
        statuses: Arc::clone(&statuses),
    };
    let interrupt = Arc::new(AtomicBool::new(false));
    let stop = Arc::clone(&interrupt);
    let seen = Arc::clone(&statuses);
    let renderer = thread::spawn(move || {
        status_child.send(b"{\"missed\": 0}\n").unwrap();
        user.write_all(b"video_open").unwrap();
        // The frame and the descriptor sent with it (SCM_RIGHTS), as the renderer's socket gets them.
        let mut data = [0u8; 64];
        let mut space = [0u64; 8];
        let mut iov = libc::iovec {
            iov_base: data.as_mut_ptr().cast(),
            iov_len: data.len(),
        };
        let mut message: libc::msghdr = unsafe { std::mem::zeroed() };
        message.msg_iov = &mut iov;
        message.msg_iovlen = 1;
        message.msg_control = space.as_mut_ptr().cast();
        message.msg_controllen = std::mem::size_of_val(&space) as _;
        let n = unsafe { libc::recvmsg(std::os::fd::AsRawFd::as_raw_fd(&peer), &mut message, 0) };
        let header = unsafe { libc::CMSG_FIRSTHDR(&message) };
        let rights = !header.is_null() && unsafe { (*header).cmsg_type } == libc::SCM_RIGHTS;
        drop(user); // the source ends
        let deadline = Instant::now() + Duration::from_secs(2);
        while seen.lock().unwrap().is_empty() && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(5));
        }
        thread::sleep(Duration::from_millis(60)); // an ended source must not spin the loop
        stop.store(true, Ordering::SeqCst);
        (data[..n as usize].to_vec(), rights, peer)
    });
    let mut w = watchdog();
    w.run_hosting(
        &parent,
        Some(&status_parent),
        &mut hook,
        &interrupt,
        POLL_INTERVAL,
    )
    .unwrap();
    let (frame, rights, _peer) = renderer.join().unwrap();
    assert_eq!(frame, b"{\"op\":\"video\"}\n");
    assert!(rights, "the descriptor went with its frame");
    assert_eq!(*statuses.lock().unwrap(), [b"{\"missed\": 0}\n".to_vec()]);
    assert!(
        w.loop_passes < 40,
        "an ended source spun the loop: {} passes",
        w.loop_passes
    );
}
