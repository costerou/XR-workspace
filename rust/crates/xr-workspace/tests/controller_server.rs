//! The panel socket server (`controller_server`) against a fake service: the cases of
//! `tests/xr_controller/test_server.py`, plus the client and buffer caps and the socket file's
//! lifetime. The other-UID refusal needs a second user and is not covered here.

use std::io::{ErrorKind, Read, Write};
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};
use std::time::Duration;

use xr_wire::controller::{MAX_CLIENTS, MAX_FRAME, Message, Request};
use xr_wire::json::{Value, parse_strict};
use xr_workspace::controller_server::{Server, Service};

struct Fake {
    state: String,
    /// Bytes of padding in every reply and snapshot.
    pad: usize,
    last: Option<Value>,
    ticks: usize,
}

impl Fake {
    fn new() -> Fake {
        Fake {
            state: "idle".into(),
            pad: 0,
            last: None,
            ticks: 0,
        }
    }
    fn snapshot(&self) -> Value {
        let mut fields = vec![("state".to_string(), Value::String(self.state.clone()))];
        if self.pad > 0 {
            fields.push(("pad".into(), Value::String("x".repeat(self.pad))));
        }
        Value::Object(fields)
    }
}

impl Service for Fake {
    fn handle(&mut self, request: &Request) -> Message {
        let mut body = vec![("ok".to_string(), Value::Bool(true))];
        if let Value::Object(f) = self.snapshot() {
            body.extend(f);
        }
        Message::Reply {
            request_id: request.request_id,
            body,
        }
    }
    fn tick(&mut self) {
        self.ticks += 1;
    }
    fn take_changed(&mut self) -> Option<Value> {
        let now = self.snapshot();
        if self.last.as_ref() == Some(&now) {
            return None;
        }
        self.last = Some(now.clone());
        Some(now)
    }
}

struct Dir(PathBuf);

impl Dir {
    fn new(tag: &str) -> Dir {
        let base = std::env::temp_dir().canonicalize().unwrap();
        let path = base.join(format!("xr-controller-server-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&path);
        std::fs::create_dir(&path).unwrap();
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o700)).unwrap();
        Dir(path)
    }
    fn socket(&self) -> PathBuf {
        self.0.join("viture-workspace").join("controller.sock")
    }
}

impl Drop for Dir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn setup(tag: &str) -> (Dir, Fake, Server) {
    let dir = Dir::new(tag);
    let mut fake = Fake::new();
    let server = Server::bind(&dir.socket(), &mut fake).unwrap();
    (dir, fake, server)
}

fn steps(server: &mut Server, fake: &mut Fake, n: usize) {
    for _ in 0..n {
        server.step(fake, Duration::ZERO).unwrap();
    }
}

fn connect(server: &mut Server, fake: &mut Fake, path: &Path) -> UnixStream {
    let c = UnixStream::connect(path).unwrap();
    c.set_read_timeout(Some(Duration::from_millis(200)))
        .unwrap();
    steps(server, fake, 1);
    c
}

/// The last whole line waiting on `c`, after letting the server run.
fn receive(server: &mut Server, fake: &mut Fake, c: &mut UnixStream) -> Value {
    steps(server, fake, 3);
    let mut buffer = vec![0; 65536];
    let n = c.read(&mut buffer).unwrap();
    let text = std::str::from_utf8(&buffer[..n]).unwrap();
    parse_strict(text.lines().last().unwrap().as_bytes()).unwrap()
}

/// Whether the server closed `c` (end of file, or reset), after letting it run.
fn closed(server: &mut Server, fake: &mut Fake, c: &mut UnixStream) -> bool {
    steps(server, fake, 3);
    let mut buffer = [0; 16];
    match c.read(&mut buffer) {
        Ok(0) => true,
        Ok(_) => false,
        Err(e) => e.kind() == ErrorKind::ConnectionReset,
    }
}

fn get<'a>(v: &'a Value, key: &str) -> Option<&'a Value> {
    v.get(key)
}

#[test]
fn status_survives_client_disconnect_and_carries_the_request_id() {
    let (dir, mut fake, mut server) = setup("status");
    let c = connect(&mut server, &mut fake, &dir.socket());
    drop(c);
    steps(&mut server, &mut fake, 2);
    assert_eq!(server.clients(), 0);
    fake.state = "running".into();
    let mut c = connect(&mut server, &mut fake, &dir.socket());
    c.write_all(b"{\"schema\":1,\"op\":\"status\",\"request_id\":7}\n")
        .unwrap();
    let reply = receive(&mut server, &mut fake, &mut c);
    assert_eq!(
        get(&reply, "state").and_then(Value::as_str),
        Some("running")
    );
    assert_eq!(get(&reply, "request_id").and_then(Value::as_int), Some(7));
    assert_eq!(get(&reply, "schema").and_then(Value::as_int), Some(1));
}

#[test]
fn a_request_without_an_id_gets_a_reply_without_one() {
    let (dir, mut fake, mut server) = setup("noid");
    let mut c = connect(&mut server, &mut fake, &dir.socket());
    c.write_all(b"{\"schema\":1,\"op\":\"status\",\"request_id\":null}\n")
        .unwrap();
    let reply = receive(&mut server, &mut fake, &mut c);
    assert_eq!(get(&reply, "ok"), Some(&Value::Bool(true)));
    assert_eq!(get(&reply, "request_id"), None);
}

#[test]
fn bad_lines_end_the_connection() {
    let (dir, mut fake, mut server) = setup("bad");
    let long_line = [vec![b'x'; MAX_FRAME], b"\n".to_vec()].concat();
    let cases: Vec<Vec<u8>> = vec![
        vec![b'x'; MAX_FRAME + 1],
        vec![b' '; MAX_FRAME], // no newline yet, but the buffer is full
        long_line,
        b"{\"schema\":2,\"op\":\"status\"}\n".to_vec(),
        b"{\"schema\":true}\n".to_vec(),
        b"{\"schema\":1,\"schema\":1}\n".to_vec(),
        b"{\"schema\":1,\"request_id\":0}\n".to_vec(),
        b"{\"schema\":1,\"x\":NaN}\n".to_vec(),
        b"[1]\n".to_vec(),
    ];
    for raw in cases {
        let mut c = connect(&mut server, &mut fake, &dir.socket());
        // A refused line may reset the connection before all of it is written.
        let _ = c.write_all(&raw);
        assert!(
            closed(&mut server, &mut fake, &mut c),
            "kept open after {:?}",
            &raw[..raw.len().min(40)]
        );
    }
    assert_eq!(server.clients(), 0);
}

#[test]
fn a_line_split_across_reads_is_one_request() {
    let (dir, mut fake, mut server) = setup("split");
    let mut c = connect(&mut server, &mut fake, &dir.socket());
    c.write_all(b"{\"schema\":1,\"op\":\"sta").unwrap();
    steps(&mut server, &mut fake, 2);
    c.write_all(b"tus\",\"request_id\":3}\n{\"schema\":1,\"op\":\"status\",\"request_id\":4}\n")
        .unwrap();
    steps(&mut server, &mut fake, 3);
    let mut buffer = vec![0; 65536];
    let n = c.read(&mut buffer).unwrap();
    let ids: Vec<i128> = std::str::from_utf8(&buffer[..n])
        .unwrap()
        .lines()
        .map(|l| {
            parse_strict(l.as_bytes())
                .unwrap()
                .get("request_id")
                .and_then(Value::as_int)
                .unwrap()
        })
        .collect();
    assert_eq!(ids, vec![3, 4]);
}

#[test]
fn a_changed_snapshot_is_pushed_to_every_client() {
    let (dir, mut fake, mut server) = setup("push");
    let mut a = connect(&mut server, &mut fake, &dir.socket());
    let mut b = connect(&mut server, &mut fake, &dir.socket());
    fake.state = "running".into();
    for c in [&mut a, &mut b] {
        let pushed = receive(&mut server, &mut fake, c);
        assert_eq!(
            pushed,
            parse_strict(br#"{"schema":1,"event":"state","state":"running"}"#).unwrap()
        );
    }
    // Unchanged: nothing more.
    steps(&mut server, &mut fake, 3);
    a.set_nonblocking(true).unwrap();
    assert_eq!(
        a.read(&mut [0; 16]).unwrap_err().kind(),
        ErrorKind::WouldBlock
    );
}

#[test]
fn the_controller_ticks_once_a_second() {
    let (_dir, mut fake, mut server) = setup("tick");
    let started = std::time::Instant::now();
    // A long timeout still wakes for each tick.
    while fake.ticks < 2 {
        server.step(&mut fake, Duration::from_secs(5)).unwrap();
    }
    let elapsed = started.elapsed();
    assert!(
        elapsed >= Duration::from_millis(1950) && elapsed < Duration::from_millis(2500),
        "{elapsed:?}"
    );
}

#[test]
fn the_33rd_client_is_closed_unanswered() {
    let (dir, mut fake, mut server) = setup("cap");
    let mut clients: Vec<UnixStream> = (0..MAX_CLIENTS)
        .map(|_| connect(&mut server, &mut fake, &dir.socket()))
        .collect();
    assert_eq!(server.clients(), MAX_CLIENTS);
    let mut extra = connect(&mut server, &mut fake, &dir.socket());
    extra.write_all(b"{\"schema\":1,\"op\":\"status\"}\n").ok();
    assert!(closed(&mut server, &mut fake, &mut extra));
    assert_eq!(server.clients(), MAX_CLIENTS);
    // The others are still served, and a freed slot is taken again.
    clients[0]
        .write_all(b"{\"schema\":1,\"op\":\"status\",\"request_id\":1}\n")
        .unwrap();
    let reply = receive(&mut server, &mut fake, &mut clients[0]);
    assert_eq!(get(&reply, "request_id").and_then(Value::as_int), Some(1));
    clients.pop();
    steps(&mut server, &mut fake, 2);
    let mut again = connect(&mut server, &mut fake, &dir.socket());
    again
        .write_all(b"{\"schema\":1,\"op\":\"status\",\"request_id\":2}\n")
        .unwrap();
    let reply = receive(&mut server, &mut fake, &mut again);
    assert_eq!(get(&reply, "request_id").and_then(Value::as_int), Some(2));
}

#[test]
fn a_reply_over_the_frame_limit_drops_the_client() {
    let (dir, mut fake, mut server) = setup("bigreply");
    let mut c = connect(&mut server, &mut fake, &dir.socket());
    fake.pad = MAX_FRAME;
    fake.last = Some(fake.snapshot()); // no push, only the reply
    c.write_all(b"{\"schema\":1,\"op\":\"status\"}\n").unwrap();
    assert!(closed(&mut server, &mut fake, &mut c));
}

#[test]
fn a_client_that_never_reads_is_dropped_at_the_output_cap() {
    let (dir, mut fake, mut server) = setup("stuck");
    let _c = connect(&mut server, &mut fake, &dir.socket());
    fake.pad = 8000;
    for i in 0..400 {
        fake.state = format!("s{i}");
        steps(&mut server, &mut fake, 1);
        if server.clients() == 0 {
            return;
        }
    }
    panic!("a client that never reads was kept");
}

#[test]
fn the_socket_is_private_and_removed_on_close() {
    let dir = Dir::new("perm");
    let mut fake = Fake::new();
    let server = Server::bind(&dir.socket(), &mut fake).unwrap();
    let parent = std::fs::metadata(dir.socket().parent().unwrap()).unwrap();
    assert_eq!(parent.permissions().mode() & 0o7777, 0o700);
    let socket = std::fs::symlink_metadata(dir.socket()).unwrap();
    assert_eq!(socket.permissions().mode() & 0o777, 0o600);
    // An existing file is never replaced.
    assert!(Server::bind(&dir.socket(), &mut fake).is_err());
    assert!(dir.socket().exists());
    drop(server);
    assert!(std::fs::symlink_metadata(dir.socket()).is_err());
}

#[test]
fn close_leaves_a_socket_that_is_not_its_own() {
    let dir = Dir::new("swap");
    let mut fake = Fake::new();
    let server = Server::bind(&dir.socket(), &mut fake).unwrap();
    std::fs::remove_file(dir.socket()).unwrap();
    std::fs::write(dir.socket(), b"someone else").unwrap();
    drop(server);
    assert_eq!(std::fs::read(dir.socket()).unwrap(), b"someone else");
}

#[test]
fn a_parent_that_is_not_private_is_refused() {
    let dir = Dir::new("public");
    let parent = dir.socket().parent().unwrap().to_path_buf();
    std::fs::create_dir(&parent).unwrap();
    std::fs::set_permissions(&parent, std::fs::Permissions::from_mode(0o755)).unwrap();
    assert!(Server::bind(&dir.socket(), &mut Fake::new()).is_err());
    let link = dir.0.join("link");
    std::os::unix::fs::symlink(&parent, &link).unwrap();
    std::fs::set_permissions(&parent, std::fs::Permissions::from_mode(0o700)).unwrap();
    assert!(Server::bind(&link.join("controller.sock"), &mut Fake::new()).is_err());
}
