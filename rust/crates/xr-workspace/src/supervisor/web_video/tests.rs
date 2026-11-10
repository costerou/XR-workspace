//! tests/native_spike/test_web_video.py, ported: checks, formats, reasons, yt-dlp, and the real probe and relay
//! (ffprobe and ffmpeg over HTTP from a local server, into a pipe, as the run does it).

use super::*;
use crate::supervisor::process::{self, Invocation, Output, RunError};
use crate::supervisor::video::{Projection, Stereo};
use crate::testdir::TestDir;
use serde_json::json;
use std::cell::RefCell;
use std::io::{BufRead, BufReader, Read as _, Write as _};
use std::net::{TcpListener, TcpStream};
use std::os::fd::{AsRawFd, OwnedFd};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};

fn web_with(sources: Value, extra: Value) -> Value {
    let mut out = json!({
        "viture_xr_video": 1, "page": "https://vr.example.com/watch/42", "title": "Sunset beach", "format": "STEREO_180_LR",
        "fov": null, "start": 0, "ua": "Mozilla/5.0 test", "sources": sources,
    });
    if let (Value::Object(o), Value::Object(e)) = (&mut out, extra) {
        o.extend(e);
    }
    out
}

fn web(extra: Value) -> Value {
    web_with(json!([{"src": "https://cdn.example.com/a.mp4", "via": "dl8"}]), extra)
}

fn url(text: &str) -> Value {
    Value::String(text.into())
}

fn fmt(p: Projection, s: Stereo, f: i64) -> Format {
    Format::new(p, s, f)
}

fn after<'a>(argv: &'a [String], flag: &str) -> &'a str {
    &argv[argv.iter().position(|a| a == flag).unwrap_or_else(|| panic!("no {flag} in {argv:?}")) + 1]
}

fn inputs(argv: &[String]) -> Vec<&str> {
    argv.iter().enumerate().filter(|(_, a)| *a == "-i").map(|(i, _)| argv[i + 1].as_str()).collect()
}

// --- checks -------------------------------------------------------------------------------------------------------

#[test]
fn urls() {
    let good = "https://cdn.example.com/v/a.mp4?sig=x&e=1";
    assert_eq!(validate_url(&url(good), false).unwrap(), good);
    let long = format!("https://x.example.com/{}", "a".repeat(4096));
    let bad = [
        "", "ftp://x/a.mp4", "file:///etc/passwd", "data:video/mp4;base64,AA", "javascript:alert(1)",
        "https://user:pw@cdn.example.com/a.mp4", "https://cdn.example.com/a b.mp4", "https://cdn.example.com/a\n.mp4",
        "https://localhost/a.mp4", "http://127.0.0.1:8080/a.mp4", "http://[::1]/a.mp4", "http://192.168.1.4/a.mp4",
        "http://10.0.0.1/a.m3u8", "http://169.254.169.254/latest", "https://cdn.example.com:99999/a.mp4", &long,
    ];
    for b in bad {
        let e = validate_url(&url(b), false).expect_err(b);
        assert!(e.is_rejection(), "{b}");
    }
    for b in [json!(null), json!(7)] {
        assert!(validate_url(&b, false).unwrap_err().is_rejection());
    }
    assert!(validate_url(&url("blob:https://vr.example.com/1234"), false).unwrap_err().message().contains("blob"));
    assert_eq!(validate_url(&url("http://127.0.0.1:8080/a.mp4"), true).unwrap(), "http://127.0.0.1:8080/a.mp4");
}

#[test]
fn web_object() {
    let w = validate_web(&web(json!({})), false).unwrap();
    assert_eq!(w.format.as_deref(), Some("STEREO_180_LR"));
    assert_eq!(
        w.sources,
        [Source { src: "https://cdn.example.com/a.mp4".into(), mime: String::new(), quality: String::new(), format: None, via: "dl8" }]
    );
    assert_eq!(request_headers(&w), [("Referer".to_owned(), "https://vr.example.com/watch/42".to_owned())]);
    // A page that is not http(s), or an odd start, is simply dropped; unknown keys or a missing marker are refused.
    assert_eq!(validate_web(&web(json!({"page": "file:///home/x.html"})), false).unwrap().page, None);
    assert_eq!(validate_web(&web(json!({"start": -3})), false).unwrap().start, 0.0);
    assert_eq!(validate_web(&web(json!({"ua": "Mozilla\r\nX-Evil: 1"})), false).unwrap().ua, None);
    let one = |s: Value| web_with(json!([s]), json!({}));
    let mut marker = web(json!({}));
    marker["viture_xr_video"] = json!(2);
    let bad = [
        web(json!({"extra": 1})),
        marker,
        web(json!({"sources": []})),
        web(json!({"sources": "x"})),
        web(json!({"format": "STEREO 180"})),
        web(json!({"fov": 1000})),
        one(json!({"src": "https://cdn.example.com/a.mp4", "cookie": "x"})),
        one(json!({"src": "https://cdn.example.com/a.mp4", "via": "elsewhere"})),
        one(json!({"src": "file:///etc/passwd"})),
        json!([]),
        json!(null),
    ];
    for b in bad {
        assert!(validate_web(&b, false).expect_err(&b.to_string()).is_rejection(), "{b}");
    }
    // Refused sources are skipped while one is left.
    let w = web_with(json!([{"src": "blob:https://x/1"}, {"src": "https://cdn.example.com/b.mp4"}]), json!({}));
    assert_eq!(validate_web(&w, false).unwrap().sources.len(), 1);
}

#[test]
fn web_request_takes_a_bare_link_or_a_web_object() {
    let Value::Object(p) = json!({"url": "https://cdn.example.com/a_360.mp4"}) else { unreachable!() };
    let w = web_request(&p, false).unwrap();
    assert_eq!((w.sources[0].src.as_str(), w.sources[0].via, w.page.as_deref()), ("https://cdn.example.com/a_360.mp4", "link", None));
    let Value::Object(p) = json!({"web": web(json!({}))}) else { unreachable!() };
    assert_eq!(web_request(&p, false).unwrap().title, "Sunset beach");
    let Value::Object(p) = json!({"url": "http://127.0.0.1/a.mp4"}) else { unreachable!() };
    assert!(web_request(&p, false).is_err());
    assert!(web_request(&p, true).is_ok());
}

// --- formats and source choice ------------------------------------------------------------------------------------

#[test]
fn player_formats() {
    use Projection::*;
    use Stereo::*;
    let f = |t: &str, fov| dl8_format(Some(t), fov).unwrap();
    assert_eq!(f("STEREO_180_LR", None), Some(fmt(P180, Sbs, 180)));
    assert_eq!(f("stereo_180_tb_spherical", None), Some(fmt(P180, Tb, 180)));
    assert_eq!(f("MONO_360", None), Some(fmt(P360, Mono, 180)));
    assert_eq!(f("STEREO_360_LR", None), Some(fmt(P360, Sbs, 180)));
    assert_eq!(f("STEREO_360_TB", None), Some(fmt(P360, Tb, 180)));
    assert_eq!(f("MONO_180", None), Some(fmt(P180, Mono, 180)));
    assert_eq!(f("STEREO_FISHEYE_LR", Some(190.0)), Some(fmt(Fisheye, Sbs, 190)));
    assert_eq!(f("STEREO_FISHEYE_LR", Some(240.0)).unwrap().fov, 220);
    assert_eq!(dl8_format(None, None).unwrap(), None);
    assert_eq!(f("SOMETHING_NEW", None), None);
    for flat in DL8_FLAT {
        assert!(dl8_format(Some(flat), None).unwrap_err().is_rejection(), "{flat}");
    }
}

#[test]
fn quality_labels() {
    for (label, h) in [("8K", 4320), ("5.7k", 3078), ("4k", 2160), ("2160p", 2160), ("1080p", 1080), ("UHD", 2160), ("", 0), ("best", 0)] {
        assert_eq!(quality_height(label), h, "{label}");
    }
}

#[test]
fn choose_the_source() {
    let w = validate_web(
        &web_with(
            json!([
                {"src": "https://cdn.example.com/beach_4k.mp4", "type": "video/mp4", "quality": "4K", "via": "dl8"},
                {"src": "https://cdn.example.com/beach_8k.mp4", "type": "video/mp4", "quality": "8K", "via": "dl8"},
                {"src": "https://cdn.example.com/beach/master.m3u8", "type": "application/x-mpegurl", "via": "dl8"},
                {"src": "https://cdn.example.com/other.mp4", "quality": "16K", "via": "resource"},
            ]),
            json!({}),
        ),
        false,
    )
    .unwrap();
    assert_eq!(choose_source(&w).src, "https://cdn.example.com/beach_8k.mp4"); // the player's own, highest
    let w = validate_web(
        &web_with(
            json!([
                {"src": "https://cdn.example.com/x.mpd", "via": "dl8"},
                {"src": "https://cdn.example.com/x.m3u8", "via": "dl8"},
                {"src": "https://cdn.example.com/x.mp4", "via": "dl8"},
            ]),
            json!({}),
        ),
        false,
    )
    .unwrap();
    assert_eq!(choose_source(&w).src, "https://cdn.example.com/x.mp4"); // same quality: progressive seeks best
    let src = |s: &str, t: &str| Source { src: s.into(), mime: t.into(), quality: String::new(), format: None, via: "link" };
    assert_eq!(source_kind(&src("https://a/x", "application/vnd.apple.mpegurl")), "hls");
    assert_eq!(source_kind(&src("https://a/x.mpd?t=1", "")), "dash");
    assert_eq!(source_kind(&src("https://a/x.mp4", "")), "progressive");
}

#[test]
fn clipboard() {
    assert_eq!(parse_clipboard("  https://cdn.example.com/a_360.mp4\n", false).unwrap(), "https://cdn.example.com/a_360.mp4");
    let web_text = web(json!({})).to_string();
    let long = "x".repeat(20000);
    for bad in ["", "   ", "hello world", r#"{"x":1}"#, &web_text, "https://a.example/x.mp4 https://b", &long] {
        assert!(parse_clipboard(bad, false).unwrap_err().is_rejection(), "{}", &bad[..bad.len().min(20)]);
    }
}

// --- failure reasons ----------------------------------------------------------------------------------------------

#[test]
fn failure_reasons_never_show_the_link() {
    let url = "https://cdn.example.com/a.mp4?token=SECRET";
    let cases = [
        (format!("[https @ 0x1] HTTP error 403 Forbidden\n{url}: Server returned 403 Forbidden"), "403"),
        ("Server returned 404 Not Found".into(), "404"),
        ("Protocol 'file' not on whitelist".into(), "other than web"),
        ("Failed to resolve hostname cdn.example.com: Name or service not known".into(), "offline"),
        ("[mov] stream 0: cenc encrypted".into(), "DRM"),
        ("Connection timed out".into(), "in time"),
        (format!("{url}: Server returned 4XX Client Error, but not one of 40{{0,1,3,4}}"), "HTTP 4xx"),
        ("HTTP error 429 Too Many Requests".into(), "429"),
        (format!("[tls @ 0x5] {url}: weird thing"), "ffmpeg could not open the stream: weird thing"),
    ];
    for (stderr, part) in cases {
        let why = explain_failure(stderr.as_bytes(), Some(1), "ffmpeg");
        assert!(why.contains(part), "{part}: {why}");
        assert!(!why.contains("SECRET") && !why.contains("cdn.example.com"), "{why}");
    }
    assert_eq!(explain_failure(b"", Some(1), "ffmpeg"), "the stream could not be opened (ffmpeg exit 1)");
    assert_eq!(failure_kind(b"x: Invalid data found when processing input"), "invalid");
    assert_eq!(failure_kind(b"HTTP error 403 Forbidden"), "http");
    assert_eq!(failure_kind(b"Server returned 5XX Server Error reply"), "http");
    assert_eq!(failure_kind(b"Connection refused"), "other");
}

// --- relay command ------------------------------------------------------------------------------------------------

#[test]
fn relay_command() {
    let headers = [("Referer".to_owned(), "https://vr.example.com/w".to_owned())];
    let argv = relay_argv("https://cdn.example.com/a.m3u8", &headers, Some("UA/1"), 12.5, Some("/usr/bin/ffmpeg"), None).unwrap();
    assert_eq!(after(&argv, "-protocol_whitelist"), "https,http,tls,tcp,crypto"); // never file:
    assert_eq!(after(&argv, "-headers"), "Referer: https://vr.example.com/w\r\n");
    assert_eq!(after(&argv, "-user_agent"), "UA/1");
    assert_eq!(after(&argv, "-ss"), "12.500");
    let at = |flag: &str| argv.iter().position(|a| a == flag).unwrap();
    assert!(at("-ss") < at("-i")); // input seek
    assert_eq!(after(&argv, "-c"), "copy"); // no re-encoding
    assert_eq!(after(&argv, "-f"), "nut");
    assert_eq!(argv.last().unwrap(), "pipe:1");
    assert_eq!(argv[0], "/usr/bin/ffmpeg");
    let plain = relay_argv("https://a.example/x.mp4", &[], None, 0.0, Some("/usr/bin/ffmpeg"), None).unwrap();
    assert!(!plain.iter().any(|a| a == "-ss" || a == "-headers" || a == "-user_agent"));
}

#[test]
fn relay_with_separate_audio() {
    let headers = [("Accept".to_owned(), "*/*".to_owned())];
    let argv = relay_argv(
        "https://media.example.net/571",
        &headers,
        Some("UA"),
        30.0,
        Some("/usr/bin/ffmpeg"),
        Some("https://media.example.net/251"),
    )
    .unwrap();
    assert_eq!(inputs(&argv), ["https://media.example.net/571", "https://media.example.net/251"]);
    assert_eq!(argv.iter().filter(|a| *a == "-ss").count(), 2);
    assert_eq!(argv.iter().filter(|a| *a == "-protocol_whitelist").count(), 2);
    let m = argv.iter().position(|a| a == "-map").unwrap();
    assert_eq!(argv[m..m + 4], ["-map", "0:v:0", "-map", "1:a:0"]);
}

// --- video pages: yt-dlp ------------------------------------------------------------------------------------------

fn yt() -> Value {
    json!({"title": "Mountain VR 180 3D SBS 8K", "tags": ["vr180", "travel"], "duration": 600, "formats": [
        {"format_id": "18", "url": "https://media.example.net/18", "protocol": "https", "vcodec": "avc1.42001E",
         "acodec": "mp4a.40.2", "width": 640, "height": 320, "tbr": 500},
        {"format_id": "sb0", "url": "https://media.example.net/sb", "protocol": "mhtml", "vcodec": "none", "acodec": "none"},
        {"format_id": "137", "url": "https://media.example.net/137", "protocol": "https", "vcodec": "avc1.640033",
         "acodec": "none", "width": 7680, "height": 3840, "tbr": 60000},
        {"format_id": "315", "url": "https://media.example.net/315", "protocol": "https", "vcodec": "vp09.00.51.08",
         "acodec": "none", "width": 3840, "height": 1920, "fps": 60, "tbr": 25000},
        {"format_id": "571", "url": "https://media.example.net/571", "protocol": "https", "vcodec": "av01.0.16M.08",
         "acodec": "none", "width": 7680, "height": 3840, "fps": 30, "tbr": 50000,
         "http_headers": {"User-Agent": "Mozilla/5.0 yt", "Accept": "*/*", "Cookie": "SID=secret", "Authorization": "x",
                          "Sec-Fetch-Mode": "navigate", "Bad\nName": "v"}},
        {"format_id": "dash", "url": "https://media.example.net/dash", "protocol": "http_dash_segments", "vcodec": "av01",
         "acodec": "none", "width": 15360, "height": 7680, "fragments": [{"path": "x"}]},
        {"format_id": "drm", "url": "https://media.example.net/drm", "protocol": "https", "vcodec": "av01", "acodec": "none",
         "width": 15360, "height": 7680, "has_drm": true},
        {"format_id": "140", "url": "https://media.example.net/140", "protocol": "https", "vcodec": "none", "acodec": "mp4a.40.2", "abr": 129},
        {"format_id": "251", "url": "https://media.example.net/251", "protocol": "https", "vcodec": "none", "acodec": "opus", "abr": 160},
    ]})
}

fn yt_format(i: usize) -> Value {
    yt()["formats"][i].clone()
}

#[test]
fn media_or_page() {
    let src = |s: &str, t: &str| Source { src: s.into(), mime: t.into(), quality: String::new(), format: None, via: "link" };
    for u in ["https://cdn.example.com/a.mp4", "https://cdn.example.com/x/master.m3u8?t=1", "https://cdn.example.com/a.MPD"] {
        assert!(looks_like_media(&src(u, "")), "{u}");
    }
    assert!(looks_like_media(&src("https://cdn.example.com/stream", "video/mp4")));
    for u in ["https://www.example.com/watch?v=abc", "https://vr.example.com/video/12345/alps-in-vr", "https://example.com/"] {
        assert!(!looks_like_media(&src(u, "")), "{u}");
    }
}

#[test]
fn best_format_and_cookie_free_headers() {
    let data = yt();
    let (video_fmt, audio) = pick_formats(&data).unwrap();
    assert_eq!(video_fmt["format_id"], "571"); // 8K AV1 decodes in hardware; 8K H.264 would not; DRM/DASH skipped
    assert_eq!(audio.unwrap()["format_id"], "251");
    let (headers, ua) = relay_headers(video_fmt);
    assert_eq!(ua.as_deref(), Some("Mozilla/5.0 yt"));
    let h = |k: &str, v: &str| (k.to_owned(), v.to_owned());
    assert_eq!(headers, [h("Accept", "*/*"), h("Sec-Fetch-Mode", "navigate")]); // no Cookie, Authorization or bad names
    let only = |i| json!({"formats": [yt_format(i)]});
    let combined = only(0);
    let (c, none) = pick_formats(&combined).unwrap();
    assert_eq!((c["format_id"].as_str(), none), (Some("18"), None));
    let drm = only(6);
    assert!(pick_formats(&drm).unwrap_err().message().contains("DRM"));
    let sb = only(1);
    assert!(pick_formats(&sb).unwrap_err().is_rejection());
}

/// A Runner stand-in: records each call and answers with `out`.
fn fake_run(calls: &RefCell<Vec<Invocation>>, out: Output) -> impl Fn(&Invocation) -> std::result::Result<Output, RunError> + '_ {
    move |inv| {
        calls.borrow_mut().push(inv.clone());
        Ok(out.clone())
    }
}

#[test]
fn yt_dlp_runs_locked_down_and_its_errors_are_clear() {
    let page = "https://www.example.com/watch?v=SECRETID";
    let calls = RefCell::new(Vec::new());
    let ok = Output { stdout: yt().to_string().into_bytes(), ..Output::default() };
    let data = resolve_page(page, &fake_run(&calls, ok), Some("/usr/bin/yt-dlp")).unwrap();
    assert_eq!(data["title"], yt()["title"]);
    let inv = calls.borrow()[0].clone();
    for flag in [
        "--ignore-config", "--no-plugin-dirs", "--no-cache-dir", "--no-cookies", "--no-cookies-from-browser", "--no-playlist",
        "--skip-download", "--dump-single-json",
    ] {
        assert!(inv.argv.iter().any(|a| a == flag), "{flag}");
    }
    assert_eq!(inv.argv[0], "/usr/bin/yt-dlp");
    assert_eq!(inv.argv[inv.argv.len() - 2..], ["--", page]);
    assert!(!inv.argv.iter().filter(|a| *a != "--no-cookies-from-browser").any(|a| a.contains("cookies-from-browser")));
    let mut keys: Vec<&str> = inv.env.iter().map(|(k, _)| k.as_str()).filter(|k| *k != "HOME").collect();
    keys.sort_unstable();
    assert_eq!(keys, ["LC_ALL", "PATH"]);

    let mut first = yt();
    first["title"] = json!("first");
    let playlist = json!({"_type": "playlist", "entries": [first]});
    let out = Output { stdout: playlist.to_string().into_bytes(), ..Output::default() };
    assert_eq!(resolve_page(page, &fake_run(&calls, out), Some("y")).unwrap()["title"], "first");

    let fail = |err: String| Output { returncode: 1, stderr: err.into_bytes(), ..Output::default() };
    let e = resolve_page(page, &fake_run(&calls, fail(format!("ERROR: Unsupported URL: {page}"))), Some("y")).unwrap_err();
    assert!(matches!(e, Error::PageUnsupported(_)), "{e:?}");
    let private = fail("ERROR: [youtube] SECRETID: Private video. Sign in if you have access".into());
    let e = resolve_page(page, &fake_run(&calls, private), Some("y")).unwrap_err();
    assert_eq!(e, Error::Rejected("yt-dlp could not get the video: Private video. Sign in if you have access".into()));
    let e = resolve_page(page, &fake_run(&calls, fail("ERROR: this video has DRM".into())), Some("y")).unwrap_err();
    assert!(e.message().contains("DRM"));
    let garbage = Output { stdout: b"not json".to_vec(), ..Output::default() };
    assert_eq!(resolve_page(page, &fake_run(&calls, garbage), Some("y")).unwrap_err().message(), "yt-dlp gave no readable answer");
    let e = resolve_page(page, &fake_run(&calls, Output::default()), Some("")).unwrap_err();
    for part in ["yt-dlp", "not installed", "web page link, not a video link"] {
        assert!(e.message().contains(part), "{part}: {e}");
    }
    let timeout = |_: &Invocation| Err(RunError::Timeout);
    assert!(resolve_page(page, &timeout, Some("y")).unwrap_err().message().contains("too long"));
}

// --- prepare and open with stand-ins ------------------------------------------------------------------------------

/// A relay stand-in: its read end is already at end of file.
struct FakeRelay {
    log: Arc<Mutex<Vec<FakeLog>>>,
    index: usize,
}

#[derive(Debug, Clone, Default)]
struct FakeLog {
    argv: Vec<String>,
    started: bool,
    stopped: bool,
}

impl Relay for FakeRelay {
    fn start(&mut self) -> io::Result<OwnedFd> {
        self.log.lock().unwrap()[self.index].started = true;
        let (r, _w) = pipe(libc::O_CLOEXEC)?;
        Ok(r)
    }

    fn stop(&mut self) {
        self.log.lock().unwrap()[self.index].stopped = true;
    }

    fn failure(&self) -> Option<String> {
        None
    }
}

fn fake_factory() -> (RelayFactory, Arc<Mutex<Vec<FakeLog>>>) {
    let log: Arc<Mutex<Vec<FakeLog>>> = Arc::default();
    let shared = log.clone();
    let factory: RelayFactory = Box::new(move |argv| {
        let mut l = shared.lock().unwrap();
        l.push(FakeLog { argv, ..FakeLog::default() });
        Box::new(FakeRelay { log: shared.clone(), index: l.len() - 1 })
    });
    (factory, log)
}

fn info(w: i64, h: i64, codec: &str, duration: Option<f64>) -> ProbeInfo {
    ProbeInfo { width: w, height: h, codec: codec.into(), fps: Some(30.0), duration, ..ProbeInfo::default() }
}

type Probed = RefCell<Vec<(String, Headers, Option<String>)>>;
type ProbeFn<'a> = Box<dyn Fn(&str, &[(String, String)], Option<&str>) -> Result<Option<ProbeInfo>> + 'a>;

fn recording_probe(probed: &Probed, answer: ProbeInfo) -> ProbeFn<'_> {
    Box::new(move |u, h, ua| {
        probed.borrow_mut().push((u.to_owned(), h.to_vec(), ua.map(str::to_owned)));
        if u.contains("forbidden") {
            return Err(Error::ProbeFailed { message: "the site refused the stream (HTTP 403)".into(), kind: "http" });
        }
        Ok(Some(answer.clone()))
    })
}

fn obj(v: Value) -> Map<String, Value> {
    let Value::Object(m) = v else { panic!("not an object") };
    m
}

fn no_page(_: &str) -> Result<Value> {
    panic!("a stream link is not resolved")
}

#[test]
fn opens_with_the_players_format_and_resumes_where_the_page_was() {
    let probed = Probed::default();
    let probe = recording_probe(&probed, info(8192, 4096, "hevc", Some(600.0)));
    let (factory, log) = fake_factory();
    let page = web_with(
        json!([{"src": "https://cdn.example.com/beach_4k.mp4", "quality": "4K"}, {"src": "https://cdn.example.com/beach_8k.mp4", "quality": "8K"}]),
        json!({"start": 42.3}),
    );
    let opened = open_web(&obj(json!({"web": page})), None, false, &probe, &no_page, factory).unwrap();
    assert_eq!((opened.format, opened.source, opened.title.as_str()), (fmt(Projection::P180, Stereo::Sbs, 180), "player", "Sunset beach"));
    assert_eq!(
        *probed.borrow(),
        [(
            "https://cdn.example.com/beach_8k.mp4".to_owned(),
            vec![("Referer".to_owned(), "https://vr.example.com/watch/42".to_owned())],
            Some("Mozilla/5.0 test".to_owned())
        )]
    );
    let l = log.lock().unwrap().clone();
    assert_eq!(after(&l[0].argv, "-ss"), "42.300");
    assert!(l[0].started);
    let snap = opened.stream.snapshot();
    assert_eq!(
        snap,
        r#"{"kind":"progressive","site":"cdn.example.com","resolved":false,"position":42.3,"duration":600.0,"seekable":true,"seeking":false}"#
    );
    assert!(!snap.contains("beach_8k")); // the link (maybe signed) is not shown or logged
    drop(opened);
    assert!(log.lock().unwrap()[0].stopped); // dropping the stream stops its relay
}

#[test]
fn a_bare_link_is_detected_from_its_name_and_a_choice_wins() {
    let probed = Probed::default();
    let probe = recording_probe(&probed, info(8192, 4096, "hevc", Some(600.0)));
    let (factory, _log) = fake_factory();
    let payload = obj(json!({"url": "https://cdn.example.com/clips/Alps_360_TB.mp4"}));
    let opened = open_web(&payload, None, false, &probe, &no_page, factory).unwrap();
    assert_eq!((opened.format, opened.source, opened.title.as_str()), (fmt(Projection::P360, Stereo::Tb, 180), "filename", "Alps_360_TB.mp4"));
    let (factory, _log) = fake_factory();
    let chosen = fmt(Projection::P180, Stereo::Mono, 180);
    let opened = open_web(&payload, Some(chosen), false, &probe, &no_page, factory).unwrap();
    assert_eq!((opened.format, opened.source), (chosen, "chosen"));
}

#[test]
fn refusals_start_no_relay() {
    let probed = Probed::default();
    let probe = recording_probe(&probed, info(8192, 4096, "hevc", Some(600.0)));
    let cases = [
        (json!({"url": "http://127.0.0.1/a.mp4"}), "local"),
        (json!({"url": "https://cdn.example.com/forbidden.mp4"}), "403"),
        (json!({"web": web(json!({"format": "MONO_FLAT"}))}), "flat"),
    ];
    for (payload, part) in cases {
        let (factory, log) = fake_factory();
        let e = open_web(&obj(payload), None, false, &probe, &no_page, factory).err().expect(part);
        assert!(e.is_rejection() && e.message().contains(part), "{part}: {e}");
        assert!(log.lock().unwrap().is_empty());
    }
}

#[test]
fn live_streams_warn_and_do_not_seek() {
    let probed = Probed::default();
    let probe = recording_probe(&probed, info(4096, 2048, "hevc", None));
    let (factory, log) = fake_factory();
    let mut opened = open_web(&obj(json!({"web": web(json!({"start": 30}))})), None, false, &probe, &no_page, factory).unwrap();
    assert!(opened.warnings.iter().any(|w| w == "live stream: no seeking"));
    assert!(!opened.stream.request_seek(10.0, 1.0));
    assert_eq!(after(&log.lock().unwrap()[0].argv, "-ss"), "30.000"); // the page's start still applies (as in Python)
    assert!(opened.stream.snapshot().contains(r#""duration":null,"seekable":false"#));
}

#[test]
fn a_page_link_plays_its_best_stream_with_the_format_from_its_title() {
    let probed = Probed::default();
    let probe = recording_probe(&probed, info(7680, 3840, "av1", Some(600.0)));
    let (factory, log) = fake_factory();
    let resolved = RefCell::new(Vec::new());
    let resolve = |u: &str| {
        resolved.borrow_mut().push(u.to_owned());
        Ok(yt())
    };
    let opened = open_web(&obj(json!({"url": "https://www.example.com/watch?v=SECRETID"})), None, false, &probe, &resolve, factory).unwrap();
    assert_eq!(*resolved.borrow(), ["https://www.example.com/watch?v=SECRETID"]);
    assert_eq!((opened.format, opened.source, opened.title.as_str()), (fmt(Projection::P180, Stereo::Sbs, 180), "title", "Mountain VR 180 3D SBS 8K"));
    let h = |k: &str, v: &str| (k.to_owned(), v.to_owned());
    assert_eq!(
        *probed.borrow(),
        [("https://media.example.net/571".to_owned(), vec![h("Accept", "*/*"), h("Sec-Fetch-Mode", "navigate")], Some("Mozilla/5.0 yt".to_owned()))]
    );
    let argv = log.lock().unwrap()[0].argv.clone();
    assert_eq!(inputs(&argv), ["https://media.example.net/571", "https://media.example.net/251"]);
    assert!(!argv.join(" ").contains("SID=secret"));
    let snap = opened.stream.snapshot();
    assert!(snap.contains(r#""site":"www.example.com","resolved":true"#), "{snap}");
    assert!(!snap.contains("SECRETID") && !snap.contains("media.example.net"));
}

#[test]
fn an_untitled_page_falls_back_to_the_picture_shape() {
    let probed = Probed::default();
    let probe = recording_probe(&probed, info(4096, 2048, "hevc", Some(60.0)));
    let (factory, _log) = fake_factory();
    let resolve = |_: &str| Ok(json!({"title": "Holiday", "formats": [yt_format(0)]}));
    let opened = open_web(&obj(json!({"url": "https://www.example.com/v/9"})), None, false, &probe, &resolve, factory).unwrap();
    assert_eq!((opened.format.projection, opened.source), (Projection::P360, "shape"));
    assert_eq!(opened.title, "Holiday");
}

#[test]
fn refusals_for_pages() {
    let html = |_: &str, _: &[(String, String)], _: Option<&str>| -> Result<Option<ProbeInfo>> {
        Err(Error::ProbeFailed { message: "the link does not lead to a playable video".into(), kind: "invalid" })
    };
    let unsupported = |_: &str| -> Result<Value> { Err(Error::PageUnsupported("no video found on that page".into())) };
    let missing = |u: &str| resolve_page(u, &|_: &Invocation| Err(RunError::Timeout), Some(""));
    let page = obj(json!({"url": "https://www.example.com/watch?v=SECRETID"}));
    let cases: [(PageResolver, &str); 2] = [
        (&unsupported, "web page link, not a video link, and yt-dlp found no video on it"),
        (&missing, "yt-dlp, which finds the video on a page, is not installed"),
    ];
    for (resolve, part) in cases {
        let (factory, log) = fake_factory();
        let e = open_web(&page, None, false, &html, resolve, factory).err().unwrap();
        let m = e.message();
        assert!(m.contains(part) && m.contains("copy the video's own link") && !m.contains("SECRETID"), "{m}");
        assert!(log.lock().unwrap().is_empty());
    }
    // A media link that turns out to be HTML (a login page) is not called a page link: the extension says "video".
    let (factory, _log) = fake_factory();
    let e = open_web(&obj(json!({"url": "https://cdn.example.com/a_360.mp4"})), None, false, &html, &unsupported, factory).err().unwrap();
    assert_eq!(e.message(), "the link does not lead to a playable video");
    // A resolved format pointing at a local address is refused like any link.
    let (factory, _log) = fake_factory();
    let local = |_: &str| Ok(json!({"formats": [{"format_id": "1", "url": "http://10.0.0.1/v", "protocol": "http", "vcodec": "hevc",
                                              "acodec": "aac", "width": 4096, "height": 2048}]}));
    let ok = |_: &str, _: &[(String, String)], _: Option<&str>| Ok(Some(info(4096, 2048, "hevc", Some(60.0))));
    assert!(open_web(&page, None, false, &ok, &local, factory).is_err());
}

#[test]
fn seek_keys_settle_into_one_restart_from_the_estimated_position() {
    let target = Target {
        url: "https://cdn.example.com/a.mp4".into(),
        audio: None,
        headers: Vec::new(),
        ua: None,
        kind: "progressive",
        site: Some("cdn.example.com".into()),
        via: "direct",
        title: "a".into(),
        hint: "a".into(),
    };
    let (factory, log) = fake_factory();
    let mut s = WebStream::new(target, Some(info(4096, 2048, "hevc", Some(600.0))), factory, Some("/usr/bin/ffmpeg".into()));
    let _fd = s.start(0.0).unwrap();
    for _ in 0..10 {
        s.advance(1.0, "playing");
    }
    s.advance(30.0, "playing"); // a stalled loop counts as at most 1 s
    s.advance(5.0, "paused"); // paused time does not count
    assert!((s.position() - 11.0).abs() < 1e-9);
    assert!(s.request_seek(60.0, 100.0) && s.request_seek(10.0, 100.1) && s.request_seek(10.0, 100.2));
    assert_eq!(s.due_seek(100.3), None); // still settling
    assert!(s.snapshot().contains(r#""seeking":true"#));
    assert_eq!(s.due_seek(100.2 + SEEK_SETTLE_S), Some(91.0));
    assert_eq!(s.due_seek(200.0), None);
    let _fd = s.start(91.0).unwrap();
    let l = log.lock().unwrap().clone();
    assert!(l[0].stopped && !l[1].stopped);
    assert_eq!(after(&l[1].argv, "-ss"), "91.000");
    assert_eq!((s.base, s.played, s.restarts), (91.0, 0.0, 2));
    // Seeking back past the start or forward past the end stays inside the video.
    s.request_seek(-500.0, 0.0);
    let back = s.due_seek(10.0).unwrap();
    let _fd = s.start(back).unwrap();
    assert!(!log.lock().unwrap()[2].argv.iter().any(|a| a == "-ss"));
    s.request_seek(1000.0, 0.0);
    let fwd = s.due_seek(10.0).unwrap();
    let _fd = s.start(fwd).unwrap();
    assert_eq!(after(&log.lock().unwrap()[3].argv, "-ss"), "597.000");
    s.stop();
    assert!(log.lock().unwrap().iter().all(|r| r.stopped));
}

// --- the real probe and relay -------------------------------------------------------------------------------------

/// A static HTTP server with byte ranges (CDNs serve MP4 that way). `/forbidden...` answers 403; `rate` (bytes a
/// second) throttles it like a real link, so ffmpeg is still downloading when a stop comes.
struct Server {
    base: String,
    hits: Arc<Mutex<Vec<String>>>,
}

fn serve(dir: PathBuf, rate: Option<f64>) -> Server {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let base = format!("http://127.0.0.1:{}", listener.local_addr().unwrap().port());
    let hits: Arc<Mutex<Vec<String>>> = Arc::default();
    let log = hits.clone();
    std::thread::spawn(move || {
        for conn in listener.incoming().flatten() {
            let (dir, log) = (dir.clone(), log.clone());
            std::thread::spawn(move || {
                let _ = answer(conn, &dir, &log, rate);
            });
        }
    });
    Server { base, hits }
}

fn answer(conn: TcpStream, dir: &Path, hits: &Mutex<Vec<String>>, rate: Option<f64>) -> io::Result<()> {
    let mut reader = BufReader::new(conn.try_clone()?);
    let mut line = String::new();
    reader.read_line(&mut line)?;
    let path = line.split_whitespace().nth(1).unwrap_or("/").to_owned();
    let mut range = None;
    loop {
        let mut h = String::new();
        if reader.read_line(&mut h)? == 0 || h == "\r\n" || h == "\n" {
            break;
        }
        if let Some((k, v)) = h.split_once(':') {
            if k.eq_ignore_ascii_case("range") {
                range = Some(v.trim().to_owned());
            }
        }
    }
    hits.lock().unwrap().push(path.clone());
    let mut out = conn;
    let status = |out: &mut TcpStream, code: &str| out.write_all(format!("HTTP/1.1 {code}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n").as_bytes());
    if path.starts_with("/forbidden") {
        return status(&mut out, "403 Forbidden");
    }
    let file = dir.join(path.split('?').next().unwrap_or("").trim_start_matches('/'));
    let Ok(data) = std::fs::read(&file) else { return status(&mut out, "404 Not Found") };
    let size = data.len();
    let (mut start, mut end) = (0, size - 1);
    if let Some(r) = range.as_deref().and_then(|r| r.strip_prefix("bytes=")) {
        let (a, b) = r.split_once('-').unwrap_or((r, ""));
        match (a.parse::<usize>(), b.parse::<usize>()) {
            (Ok(a), Ok(b)) => (start, end) = (a, b),
            (Ok(a), Err(_)) => start = a,
            (Err(_), Ok(n)) => start = size.saturating_sub(n),
            _ => {}
        }
    }
    end = end.min(size - 1);
    let mime = match file.extension().and_then(|e| e.to_str()) {
        Some("mp4") => "video/mp4",
        Some("m3u8") => "application/vnd.apple.mpegurl",
        Some("ts") => "video/mp2t",
        _ => "text/html",
    };
    let code = if range.is_some() { "206 Partial Content" } else { "200 OK" };
    let mut head = format!("HTTP/1.1 {code}\r\nContent-Type: {mime}\r\nAccept-Ranges: bytes\r\nContent-Length: {}\r\nConnection: close\r\n", end + 1 - start);
    if range.is_some() {
        head += &format!("Content-Range: bytes {start}-{end}/{size}\r\n");
    }
    out.write_all(format!("{head}\r\n").as_bytes())?;
    let t0 = Instant::now();
    let mut sent = 0;
    for chunk in data[start..=end].chunks(65536) {
        out.write_all(chunk)?;
        sent += chunk.len();
        if let Some(rate) = rate {
            let due = Duration::from_secs_f64(sent as f64 / rate);
            if let Some(wait) = due.checked_sub(t0.elapsed()) {
                std::thread::sleep(wait);
            }
        }
    }
    Ok(())
}

fn ffmpeg(args: &[&str]) {
    let status = std::process::Command::new("/usr/bin/ffmpeg").args(["-v", "error", "-y"]).args(args).status().unwrap();
    assert!(status.success(), "ffmpeg {args:?}");
}

fn have_tools() -> bool {
    let ok = process::executable(Path::new("/usr/bin/ffmpeg")) && process::executable(Path::new("/usr/bin/ffprobe"));
    if !ok {
        eprintln!("skipped: ffmpeg/ffprobe not installed");
    }
    ok
}

/// The test clips and their servers, made once and removed when the test program exits.
struct Media {
    dir: PathBuf,
    plain: Server,
    throttled: Server,
}

static MEDIA_DIR: OnceLock<PathBuf> = OnceLock::new();

extern "C" fn remove_media() {
    if let Some(dir) = MEDIA_DIR.get() {
        let _ = std::fs::remove_dir_all(dir);
    }
}

fn media() -> &'static Media {
    static MEDIA: OnceLock<Media> = OnceLock::new();
    MEDIA.get_or_init(|| {
        let tmp = TestDir::new("web-video");
        let dir = tmp.path().to_owned();
        std::mem::forget(tmp); // removed at exit instead
        MEDIA_DIR.set(dir.clone()).unwrap();
        // SAFETY: registers a plain function to run at exit.
        unsafe { libc::atexit(remove_media) };
        let d = |name: &str| dir.join(name).to_string_lossy().into_owned();
        ffmpeg(&[
            "-f", "lavfi", "-i", "testsrc2=size=1024x512:rate=30:duration=6", "-f", "lavfi", "-i", "sine=f=440:duration=6",
            "-c:v", "libx264", "-preset", "ultrafast", "-g", "30", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
            "-movflags", "+faststart", &d("beach_180_LR.mp4"),
        ]);
        std::fs::create_dir(dir.join("hls")).unwrap();
        ffmpeg(&["-i", &d("beach_180_LR.mp4"), "-c", "copy", "-f", "hls", "-hls_time", "2", "-hls_playlist_type", "vod", &d("hls/index.m3u8")]);
        std::fs::write(
            dir.join("watch"),
            format!("<!doctype html><html><head><title>Talk show</title></head><body><p>{}</p></body></html>", "x".repeat(2000)),
        )
        .unwrap();
        std::fs::write(
            dir.join("vrpage"),
            "<!doctype html><html><head><title>Alps VR180 3D SBS</title></head><body><video controls src=\"beach_180_LR.mp4\"></video></body></html>",
        )
        .unwrap();
        // 60 s at 20 Mbit/s (~150 MB): far more than a stop test downloads.
        ffmpeg(&[
            "-f", "lavfi", "-i", "testsrc2=size=1280x640:rate=30:duration=4", "-f", "lavfi", "-i", "sine=f=440:duration=4",
            "-c:v", "libx264", "-preset", "ultrafast", "-b:v", "20M", "-g", "30", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
            &d("short.mp4"),
        ]);
        ffmpeg(&["-stream_loop", "14", "-i", &d("short.mp4"), "-c", "copy", "-movflags", "+faststart", &d("long_180_LR.mp4")]);
        let plain = serve(dir.clone(), None);
        let throttled = serve(dir.clone(), Some(25e6)); // a 200 Mbit/s link
        Media { dir, plain, throttled }
    })
}

/// Read the relay's pipe to its end (what the renderer would get).
fn drain(fd: OwnedFd) -> Vec<u8> {
    let mut data = Vec::new();
    std::fs::File::from(fd).read_to_end(&mut data).unwrap();
    data
}

/// ffprobe of relayed bytes, with packet counts.
fn probe_bytes(data: &[u8], name: &str) -> Value {
    let p = media().dir.join(format!("{name}.nut"));
    std::fs::write(&p, data).unwrap();
    let out = std::process::Command::new("/usr/bin/ffprobe")
        .args(["-v", "error", "-print_format", "json", "-show_streams", "-show_format", "-count_packets"])
        .arg(&p)
        .output()
        .unwrap();
    assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
    let _ = std::fs::remove_file(&p);
    serde_json::from_slice(&out.stdout).unwrap()
}

fn video_stream(p: &Value) -> &Value {
    p["streams"].as_array().unwrap().iter().find(|s| s["codec_type"] == "video").expect("a video stream")
}

fn packets(p: &Value) -> i64 {
    video_stream(p)["nb_read_packets"].as_str().unwrap().parse().unwrap()
}

fn referer() -> Headers {
    vec![("Referer".to_owned(), "https://vr.example.com/".to_owned())]
}

fn real_probe(u: &str, h: &[(String, String)], ua: Option<&str>) -> Result<Option<ProbeInfo>> {
    probe_url(u, h, ua, &process::run, None)
}

fn relay(u: &str, start: f64) -> (StreamRelay, OwnedFd) {
    let mut r = StreamRelay::new(relay_argv(u, &referer(), Some("UA/1"), start, None, None).unwrap(), RELAY_BUFFER);
    let fd = Relay::start(&mut r).unwrap();
    (r, fd)
}

#[test]
fn probe_over_http() {
    if !have_tools() {
        return;
    }
    let base = &media().plain.base;
    let info = real_probe(&format!("{base}/beach_180_LR.mp4"), &referer(), Some("UA/1")).unwrap().unwrap();
    assert_eq!((info.width, info.height, info.codec.as_str()), (1024, 512, "h264"));
    assert!((info.duration.unwrap() - 6.0).abs() < 0.2);
    let hls = real_probe(&format!("{base}/hls/index.m3u8"), &[], None).unwrap().unwrap();
    assert_eq!((hls.width, hls.height), (1024, 512));
    let e = real_probe(&format!("{base}/forbidden.mp4?token=SECRET"), &[], None).unwrap_err();
    assert!(e.message().contains("403") && !e.message().contains("SECRET"), "{e}");
    assert!(matches!(e, Error::ProbeFailed { kind: "http", .. }));
    let e = real_probe(&format!("{base}/missing.mp4"), &[], None).unwrap_err();
    assert!(e.message().contains("404"), "{e}");
    // A page is not a video: ffprobe's "invalid data" kind, which open_web turns into the page-link refusal.
    let e = real_probe(&format!("{base}/watch"), &[], None).unwrap_err();
    assert!(matches!(e, Error::ProbeFailed { kind: "invalid", .. }), "{e:?}");
}

#[test]
fn progressive_and_hls_relay_copy_every_frame() {
    if !have_tools() {
        return;
    }
    for (u, name) in [("/beach_180_LR.mp4", "every-mp4"), ("/hls/index.m3u8", "every-hls")] {
        let (mut r, fd) = relay(&format!("{}{u}", media().plain.base), 0.0);
        let data = drain(fd);
        Relay::stop(&mut r);
        assert_eq!(r.returncode(), Some(0), "{u}");
        assert_eq!(r.failure(), None);
        assert!(r.finished() && r.bytes_in() == data.len() as u64 && r.bytes_out() == data.len() as u64 && r.buffered() == 0);
        let p = probe_bytes(&data, name);
        assert_eq!(p["format"]["format_name"], "nut");
        let v = video_stream(&p);
        assert_eq!((v["codec_name"].as_str(), v["width"].as_i64(), v["height"].as_i64()), (Some("h264"), Some(1024), Some(512))); // copied
        assert_eq!(packets(&p), 180, "{u}"); // 6 s at 30 fps: every frame
        assert!(p["streams"].as_array().unwrap().iter().any(|s| s["codec_type"] == "audio"));
    }
}

#[test]
fn relay_from_a_position() {
    if !have_tools() {
        return;
    }
    // MP4: from the keyframe before 4 s (about the last 2 s). HLS: from the start of the segment holding 4 s.
    for (u, most, name) in [("/beach_180_LR.mp4", 100, "from-mp4"), ("/hls/index.m3u8", 150, "from-hls")] {
        let (mut r, fd) = relay(&format!("{}{u}", media().plain.base), 4.0);
        let n = packets(&probe_bytes(&drain(fd), name));
        Relay::stop(&mut r);
        assert!(n > 30 && n < most, "{u}: {n}");
    }
}

#[test]
fn a_paused_renderer_does_not_block_stop() {
    if !have_tools() {
        return;
    }
    let (mut r, fd) = relay(&format!("{}/beach_180_LR.mp4", media().plain.base), 0.0);
    std::thread::sleep(Duration::from_millis(500)); // nobody reads: ffmpeg and the writer wait on the full pipe
    let t0 = Instant::now();
    Relay::stop(&mut r);
    drop(fd);
    assert!(t0.elapsed() < Duration::from_millis(3500), "{:?}", t0.elapsed());
    assert_eq!(r.failure(), None); // stopped on purpose
}

#[test]
fn a_refused_stream_is_a_failure_with_a_reason() {
    if !have_tools() {
        return;
    }
    let (r, fd) = relay(&format!("{}/forbidden.mp4", media().plain.base), 0.0);
    assert_eq!(drain(fd), b"");
    let deadline = Instant::now() + Duration::from_secs(5);
    while r.returncode().is_none() && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(50));
    }
    assert_ne!(r.returncode(), Some(0));
    assert!(r.returncode().is_some());
    let why = r.failure().unwrap();
    assert!(why.contains("403"), "{why}");
}

fn pipe_size(fd: i32) -> i32 {
    // SAFETY: fcntl on a descriptor number has no memory effects.
    unsafe { libc::fcntl(fd, libc::F_GETPIPE_SZ) }
}

#[test]
fn relay_pipes_are_large() {
    if !have_tools() {
        return;
    }
    let limit: i32 = std::fs::read_to_string("/proc/sys/fs/pipe-max-size").unwrap().trim().parse().unwrap();
    let want = if limit >= 65536 { RELAY_PIPE.min(limit) } else { 65536 };
    let (mut r, fd) = relay(&format!("{}/beach_180_LR.mp4", media().plain.base), 0.0);
    assert_eq!(pipe_size(fd.as_raw_fd()), want);
    assert_eq!(pipe_size(r.stdout_fd().unwrap()), want);
    Relay::stop(&mut r);
}

#[test]
fn the_run_end_to_end() {
    if !have_tools() {
        return;
    }
    // A web request object in, a NUT pipe for the renderer out (real probe and relay).
    let m = media();
    let base = &m.plain.base;
    let page = web_with(
        json!([{"src": format!("{base}/hls/index.m3u8"), "type": "application/x-mpegurl"}, {"src": format!("{base}/beach_180_LR.mp4"), "quality": "1080p"}]),
        json!({"start": 2.5}),
    );
    let opened = open_web(&obj(json!({"web": page})), None, true, &real_probe, &no_page, stream_relay_factory()).unwrap();
    assert_eq!((opened.format, opened.source, opened.title.as_str()), (fmt(Projection::P180, Stereo::Sbs, 180), "player", "Sunset beach"));
    let WebOpened { fd, stream, .. } = opened;
    let p = probe_bytes(&drain(fd), "e2e");
    assert_eq!((p["format"]["format_name"].as_str(), video_stream(&p)["width"].as_i64()), (Some("nut"), Some(1024)));
    assert_eq!(packets(&p), 180); // from the start: 2.5 s leaves under 5 s of this 6 s clip
    assert!(m.plain.hits.lock().unwrap().iter().any(|h| h.contains("/beach_180_LR.mp4"))); // the labelled file was chosen
    assert!(stream.snapshot().contains(r#""duration":6.0"#));
    assert_eq!(stream.failure(), None);
}

#[test]
fn page_links_with_the_real_yt_dlp() {
    if !have_tools() {
        return;
    }
    let Some(ytdlp) = ytdlp_path() else {
        eprintln!("skipped: yt-dlp not installed");
        return;
    };
    // The installed yt-dlp and ffprobe against local pages: one embedding a 180° SBS clip (format from the page title),
    // one with no video (the "web page link" refusal, not an exit code).
    let base = &media().plain.base;
    let resolve = |u: &str| resolve_page(u, &process::run, Some(&ytdlp));
    let opened = open_web(&obj(json!({"url": format!("{base}/vrpage")})), None, true, &real_probe, &resolve, stream_relay_factory()).unwrap();
    // yt-dlp numbers generic entries.
    assert_eq!((opened.format, opened.source, opened.title.as_str()), (fmt(Projection::P180, Stereo::Sbs, 180), "title", "Alps VR180 3D SBS (1)"));
    let WebOpened { fd, stream, .. } = opened;
    assert_eq!(video_stream(&probe_bytes(&drain(fd), "ytdlp"))["width"], 1024);
    drop(stream);
    let e = open_web(&obj(json!({"url": format!("{base}/watch?v=SECRETID")})), None, true, &real_probe, &resolve, stream_relay_factory())
        .err()
        .unwrap();
    let m = e.message();
    assert!(m.contains(PAGE_LINK) && m.contains(PAGE_HELP), "{m}");
    assert!(!m.contains("SECRETID") && !m.contains("127.0.0.1") && !m.contains("exit"), "{m}");
}

// Stopping the real relay while ffmpeg downloads (every seek and exit does): milliseconds, not the seconds a SIGTERM'd
// ffmpeg blocked on its full output pipe would hold the supervisor loop for.

fn long_url() -> String {
    format!("{}/long_180_LR.mp4", media().throttled.base)
}

fn stopped_quickly(r: &mut StreamRelay) {
    let t0 = Instant::now();
    Relay::stop(r);
    assert!(t0.elapsed() < Duration::from_secs(1), "{:?}", t0.elapsed());
    assert!(r.returncode().is_some()); // ffmpeg is gone, not left running
    assert_eq!(r.failure(), None); // stopped on purpose
}

fn wait_until(what: impl Fn() -> bool) -> bool {
    let deadline = Instant::now() + Duration::from_secs(10);
    while !what() && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(50));
    }
    what()
}

#[test]
fn stop_while_downloading_with_a_paused_renderer() {
    if !have_tools() {
        return;
    }
    let mut r = StreamRelay::new(relay_argv(&long_url(), &[], None, 0.0, None, None).unwrap(), RELAY_BUFFER);
    let _fd = Relay::start(&mut r).unwrap();
    // Nobody reads: the renderer's pipe fills, ffmpeg keeps filling the read-ahead.
    assert!(wait_until(|| r.bytes_in() >= 4 << 20), "{}", r.bytes_in());
    stopped_quickly(&mut r);
}

#[test]
fn stop_with_a_full_read_ahead() {
    if !have_tools() {
        return;
    }
    let mut r = StreamRelay::new(relay_argv(&long_url(), &[], None, 0.0, None, None).unwrap(), 2 << 20); // fills in well under a second
    let _fd = Relay::start(&mut r).unwrap();
    assert!(wait_until(|| r.buffered() >= r.limit()), "{}", r.buffered());
    std::thread::sleep(Duration::from_millis(300)); // ffmpeg now blocks writing to its own stdout pipe
    stopped_quickly(&mut r);
}

#[test]
fn stop_while_the_renderer_reads() {
    if !have_tools() {
        return;
    }
    let mut r = StreamRelay::new(relay_argv(&long_url(), &[], None, 0.0, None, None).unwrap(), RELAY_BUFFER);
    let fd = Relay::start(&mut r).unwrap();
    let reader = std::thread::spawn(move || drain(fd).len());
    std::thread::sleep(Duration::from_secs(1));
    stopped_quickly(&mut r);
    let deadline = Instant::now() + Duration::from_secs(5);
    while !reader.is_finished() && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(20));
    }
    assert!(reader.is_finished()); // the renderer's pipe ended: the player sees the end of the stream
    assert!(reader.join().unwrap() > 0);
}

#[test]
fn a_seek_does_not_hold_the_supervisor_loop() {
    if !have_tools() {
        return;
    }
    // WebStream.start(at) is what a seek runs on the supervisor loop.
    let target = Target {
        url: long_url(),
        audio: None,
        headers: Vec::new(),
        ua: None,
        kind: "progressive",
        site: Some("127.0.0.1".into()),
        via: "direct",
        title: "t".into(),
        hint: "t".into(),
    };
    let duration = ProbeInfo { duration: Some(60.0), ..ProbeInfo::default() };
    let mut stream = WebStream::new(target, Some(duration), stream_relay_factory(), None);
    let mut fds = vec![stream.start(0.0).unwrap()];
    std::thread::sleep(Duration::from_secs(1));
    for at in [30.0, 10.0] {
        let t0 = Instant::now();
        fds.push(stream.start(at).unwrap());
        assert!(t0.elapsed() < Duration::from_secs(1), "{:?}", t0.elapsed());
        let fd = fds.last().unwrap().as_raw_fd();
        assert_ne!(poll(&[(fd, libc::POLLIN)], 10_000)[0] & libc::POLLIN, 0); // the new relay delivers
        assert_eq!(stream.base, at);
    }
    stream.stop();
    assert_eq!(stream.failure(), None);
}
