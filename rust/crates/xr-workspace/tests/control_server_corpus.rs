//! Holds `view_controls::socket::Server` to the shipped Python's `PrivateServer`, `control_request` and
//! `serve_without_renderer`: `rust/corpus/sockets/server.json` (`tests/native_spike/control_server_corpus.py`), every
//! run frame case of `frames.json` and the socket's own, each sent by a real client to a real socket. The same verdict
//! (`taken`, `answered`, `dropped`), the same request id taken, and the same reply bytes. A case whose payload another
//! module checks (`owner`) is taken here, as `xr_wire::run_control` documents.

use std::io::{Read, Write};
use std::os::fd::AsRawFd;
use std::os::unix::net::UnixStream;
use std::path::Path;
use std::time::Duration;

use serde_json::Value as Json;
use xr_workspace::view_controls::socket::{Body, Server};

fn corpus() -> Json {
    let path = concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../corpus/sockets/server.json"
    );
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}

fn bytes(write: &Json) -> Vec<u8> {
    if let Some(text) = write["text"].as_str() {
        return text.as_bytes().to_vec();
    }
    let h = write["hex"].as_str().unwrap();
    (0..h.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&h[i..i + 2], 16).unwrap())
        .collect()
}

fn body(text: &str) -> Body {
    match xr_wire::json::parse(
        text.as_bytes(),
        xr_wire::json::Limits {
            max_bytes: 1 << 20,
            max_depth: xr_wire::json::MAX_DEPTH,
        },
    )
    .unwrap()
    {
        xr_wire::json::Value::Object(entries) => entries,
        other => panic!("not an object: {other:?}"),
    }
}

/// A client of `server`, through its directory (a 128 character run id is past the socket path limit).
fn connect(server: &Server) -> UnixStream {
    let dir = std::fs::File::open(server.path().parent().unwrap()).unwrap();
    UnixStream::connect(Path::new(&format!(
        "/proc/self/fd/{}/control.sock",
        dir.as_raw_fd()
    )))
    .unwrap()
}

fn serve(
    case: &Json,
    session: &str,
    run_body: &Body,
    hold: (Duration, &Body),
) -> (String, Json, String) {
    let dir = std::env::temp_dir().join(format!(
        "xr-control-server-{}-{}",
        std::process::id(),
        rand_suffix()
    ));
    std::fs::create_dir(&dir).unwrap();
    std::fs::set_permissions(&dir, std::os::unix::fs::PermissionsExt::from_mode(0o700)).unwrap();
    let mut server = Server::bind(&dir, case["run_id"].as_str().unwrap(), session).unwrap();
    for earlier in case["earlier"].as_array().unwrap() {
        let mut c = connect(&server);
        c.write_all(earlier.as_str().unwrap().as_bytes()).unwrap();
        server.control_request(|| run_body.clone());
    }
    let client = case["writes"].as_array().map(|writes| {
        let mut c = connect(&server);
        let writes = writes.clone();
        let close = case["close"].as_bool().unwrap_or(false);
        std::thread::spawn(move || {
            let mut got = Vec::new();
            for w in &writes {
                std::thread::sleep(Duration::from_millis(w["pause_ms"].as_u64().unwrap_or(0)));
                if c.write_all(&bytes(w)).is_err() {
                    return got;
                }
            }
            if !close {
                let _ = c.read_to_end(&mut got);
            }
            got
        })
    });
    let taken = match case["mode"].as_str().unwrap() {
        "run" => server.control_request(|| run_body.clone()),
        _ => {
            server.serve_without_renderer(hold.0, || hold.1.clone());
            None
        }
    };
    let got = client.map(|t| t.join().unwrap()).unwrap_or_default();
    drop(server);
    std::fs::remove_dir_all(&dir).unwrap();
    let reply = String::from_utf8(got).unwrap();
    match taken {
        Some(frame) => ("taken".into(), Json::from(frame.request_id), reply),
        None if !reply.is_empty() => ("answered".into(), Json::Null, reply),
        None => ("dropped".into(), Json::Null, reply),
    }
}

fn rand_suffix() -> u64 {
    use std::sync::atomic::{AtomicU64, Ordering};
    static N: AtomicU64 = AtomicU64::new(0);
    N.fetch_add(1, Ordering::Relaxed)
}

#[test]
fn the_server_serves_every_case_as_the_python_does() {
    let corpus = corpus();
    let session = corpus["session"].as_str().unwrap();
    let run_body = body(corpus["run_body"].as_str().unwrap());
    let hold_body = body(corpus["hold_body"].as_str().unwrap());
    let hold = Duration::from_secs_f64(corpus["hold_seconds"].as_f64().unwrap());
    let cases = corpus["cases"].as_array().unwrap();
    assert!(cases.len() > 90, "{} cases", cases.len());
    let mut wrong = Vec::new();
    for case in cases {
        let (verdict, request_id, reply) = serve(case, session, &run_body, (hold, &hold_body));
        let owned = case.get("owner").is_some();
        let want = if owned {
            "taken"
        } else {
            case["verdict"].as_str().unwrap()
        };
        if verdict != want
            || (!owned && request_id != case["request_id"])
            || (!owned && reply != case["reply"].as_str().unwrap())
        {
            wrong.push(format!(
                "{}: got {verdict} {request_id} {reply:?}, want {want} {} {}",
                case["about"], case["request_id"], case["reply"]
            ));
        }
    }
    assert!(wrong.is_empty(), "{wrong:#?}");
}
