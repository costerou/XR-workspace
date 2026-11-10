//! VR video from the web (tools/native_spike/web_video.py, docs/web-video.md): a direct stream link, or a video page
//! link resolved with yt-dlp.
//!
//! The run checks the request, probes the stream with ffprobe and starts a relay: an ffmpeg process that copies the
//! stream (no re-encoding) into NUT on a pipe. The renderer gets the pipe's read end as the descriptor it already
//! accepts for local files, so it never touches the network or a path. Only the relay's ffmpeg talks to the site,
//! limited to http(s)/tls/tcp/crypto (no `file:`). Reasons never contain the link (it may hold a signed token).
//!
//! Differences from the Python module, all deliberate:
//! - Local addresses are refused more strictly: numeric host forms the resolver accepts (`2130706433`, `0x7f.1`,
//!   `127.1`) count as IPv4, shared (100.64/10) and IETF (192.0.0/24) space is local, and IPv6 must be global unicast
//!   (2000::/3 minus 2001::/23, 2001:db8::/32 and 6to4 2002::/16, which can embed a private IPv4).
//! - A link whose host part is not ASCII, or a bracketed host that is not IPv6, is refused as invalid.
//! - yt-dlp's per-format request headers are passed on in name order (serde_json keeps object keys sorted).

use std::collections::VecDeque;
use std::io;
use std::os::fd::{AsRawFd, FromRawFd, OwnedFd, RawFd};
use std::os::unix::process::{CommandExt, ExitStatusExt};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use serde_json::{Map, Value, json};

use super::process::{self, Invocation, RunError, Runner};
use super::video::{self, Format, ProbeInfo, Projection, Stereo};
use super::error::{Error, Result};
use super::json as js;

pub const YTDLP_TIMEOUT: Duration = Duration::from_secs(60);
/// `-J` of a long page with many formats is a few MB.
pub const YTDLP_MAX_OUTPUT: usize = 32 << 20;
pub const MEDIA_EXT: [&str; 12] =
    [".mp4", ".m4v", ".webm", ".mkv", ".mov", ".m3u8", ".mpd", ".ts", ".m2ts", ".avi", ".ogv", ".flv"];
pub const PAGE_LINK: &str = "that's a web page link, not a video link";
pub const PAGE_HELP: &str = "copy the video's own link instead";
pub const NO_YTDLP: &str = "yt-dlp, which finds the video on a page, is not installed (it is in the Arch repos: yt-dlp)";
/// yt-dlp request headers passed on to the relay; anything carrying a login is dropped (cookies-free by design).
const DROP_HEADERS: [&str; 3] = ["cookie", "authorization", "proxy-authorization"];
/// yt-dlp format protocols ffmpeg can read directly.
const RELAY_PROTOCOLS: [&str; 4] = ["https", "http", "m3u8", "m3u8_native"];

pub const MAX_URL: usize = 4096;
pub const MAX_SOURCES: usize = 8;
pub const MAX_WEB_TITLE: usize = 200;
pub const MAX_UA: usize = 256;
/// The control frame limit bounds the whole request anyway.
pub const MAX_CLIPBOARD: usize = 16384;
pub const MAX_START_S: f64 = 48.0 * 3600.0;
pub const WEB_PROBE_TIMEOUT: Duration = Duration::from_secs(25);
/// Read-ahead between ffmpeg and the renderer: ~10 s of 8K at 100 Mbit/s.
pub const RELAY_BUFFER: usize = 128 << 20;
pub const RELAY_CHUNK: usize = 1 << 20;
/// Kernel buffer of the two relay pipes (ffmpeg -> run, run -> renderer), up from 64 KiB: 16x fewer reads and
/// wake-ups per MiB. 1 MiB is fs.pipe-max-size's default; a lower limit just keeps the kernel's size.
pub const RELAY_PIPE: i32 = 1 << 20;
/// Seek keys within this long are combined into one relay restart.
pub const SEEK_SETTLE_S: f64 = 0.4;
/// A seek never lands closer than this to the end.
pub const END_MARGIN_S: f64 = 3.0;
/// The relay's ffmpeg and ffprobe may use only these protocols: no file:, no pipes, no other schemes from a playlist.
pub const NET_PROTOCOLS: &str = "https,http,tls,tcp,crypto";
/// Key of a `{"web"}` request object (version 1).
pub const MARKER: &str = "viture_xr_video";

/// Player `format` tokens a `{"web"}` request may carry (dl8-video style).
fn dl8_known(token: &str) -> Option<(Projection, Stereo)> {
    use Projection::*;
    use Stereo::*;
    Some(match token {
        "MONO_360" => (P360, Mono),
        "STEREO_360_LR" => (P360, Sbs),
        "STEREO_360_TB" => (P360, Tb),
        "MONO_180" => (P180, Mono),
        "STEREO_180_LR" | "STEREO_180_LR_SPHERICAL" => (P180, Sbs),
        "STEREO_180_TB" | "STEREO_180_TB_SPHERICAL" => (P180, Tb),
        // The fisheye base format: the fov comes from the request.
        "STEREO_FISHEYE_LR" => (Fisheye, Sbs),
        _ => return None,
    })
}
pub const DL8_FLAT: [&str; 5] = ["MONO_FLAT", "STEREO_FLAT_LR", "STEREO_FLAT_LR_SQUARE", "STEREO_FLAT_TB", "STEREO_FLAT_TB_SQUARE"];
const HLS_TYPES: [&str; 3] = ["application/x-mpegurl", "application/vnd.apple.mpegurl", "audio/mpegurl"];
const DASH_TYPES: [&str; 1] = ["application/dash+xml"];
const WEB_KEYS: [&str; 8] = ["page", "title", "format", "fov", "start", "sources", "ua", MARKER];
const SOURCE_KEYS: [&str; 5] = ["src", "type", "quality", "format", "via"];
/// Where a source came from, in rank order: the player first; `page` is a page link.
pub const VIA: [&str; 5] = ["dl8", "video", "resource", "link", "page"];

// --- Python text helpers ------------------------------------------------------------------------------------------

/// Python's `str.isspace()` (what `\s`, `strip()` and `split()` use): Unicode white space plus the separators
/// U+001C..U+001F.
fn py_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

fn py_strip(text: &str) -> &str {
    text.trim_matches(py_space)
}

/// `\w`: a Unicode letter, digit or underscore.
fn word(c: char) -> bool {
    c.is_alphanumeric() || c == '_'
}

/// Python's `str.splitlines()`.
fn splitlines(text: &str) -> Vec<&str> {
    let mut lines = Vec::new();
    let mut start = 0;
    let mut chars = text.char_indices().peekable();
    while let Some((i, c)) = chars.next() {
        if matches!(c, '\n' | '\r' | '\u{b}' | '\u{c}' | '\u{1c}' | '\u{1d}' | '\u{1e}' | '\u{85}' | '\u{2028}' | '\u{2029}') {
            lines.push(&text[start..i]);
            let mut end = i + c.len_utf8();
            if c == '\r' && chars.peek().is_some_and(|(_, n)| *n == '\n') {
                chars.next();
                end += 1;
            }
            start = end;
        }
    }
    if start < text.len() {
        lines.push(&text[start..]);
    }
    lines
}

/// Printable ASCII only: these strings end up in HTTP headers or ffmpeg arguments.
fn printable(value: Option<&Value>, limit: usize) -> Option<&str> {
    let s = value?.as_str()?;
    (s.chars().count() <= limit && s.bytes().all(|b| (0x20..0x7f).contains(&b))).then_some(s)
}

/// `[A-Za-z0-9_]{1,64}`, the whole text.
fn token(text: &str) -> bool {
    (1..=64).contains(&text.len()) && text.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_')
}

fn has_control(text: &str) -> bool {
    text.chars().any(|c| (c as u32) <= 0x20 || c as u32 == 0x7f)
}

// --- links --------------------------------------------------------------------------------------------------------

/// The parts of an http(s) link that matter here, as Python's `urlsplit` gives them.
#[derive(Debug, Clone, PartialEq)]
struct Url<'a> {
    netloc: &'a str,
    path: &'a str,
}

fn split(url: &str) -> Url<'_> {
    let after = url.split_once("//").map_or("", |(_, rest)| rest);
    let netloc_end = after.find(['/', '?', '#']).unwrap_or(after.len());
    let (netloc, rest) = after.split_at(netloc_end);
    let path_end = rest.find(['?', '#']).unwrap_or(rest.len());
    Url { netloc, path: &rest[..path_end] }
}

/// `urlsplit(url).hostname`: lowercased, brackets removed. None if there is none or it is malformed.
fn hostname(netloc: &str) -> Option<String> {
    let hostinfo = netloc.rsplit_once('@').map_or(netloc, |(_, h)| h);
    let host = match hostinfo.split_once('[') {
        Some((_, bracketed)) => bracketed.split_once(']').map_or(bracketed, |(h, _)| h),
        None => hostinfo.split_once(':').map_or(hostinfo, |(h, _)| h),
    };
    (!host.is_empty()).then(|| host.to_lowercase())
}

/// The link's port text after the host, if any.
fn port(netloc: &str) -> &str {
    let hostinfo = netloc.rsplit_once('@').map_or(netloc, |(_, h)| h);
    let rest = match hostinfo.split_once('[') {
        Some((_, bracketed)) => bracketed.split_once(']').map_or("", |(_, r)| r),
        None => hostinfo,
    };
    rest.split_once(':').map_or("", |(_, p)| p)
}

fn parse_ipv6(text: &str) -> Option<std::net::Ipv6Addr> {
    text.split_once('%').map_or(text, |(a, _)| a).parse().ok()
}

/// What the resolver reads as an IPv4 address, inet_aton style: 1 to 4 parts, each decimal, octal (leading 0) or hex
/// (0x), the last part filling the remaining bytes.
fn inet_aton(text: &str) -> Option<u32> {
    let parts: Vec<&str> = text.split('.').collect();
    if parts.len() > 4 {
        return None;
    }
    let mut values = Vec::new();
    for p in &parts {
        let (digits, radix) = if let Some(h) = p.strip_prefix("0x").or_else(|| p.strip_prefix("0X")) {
            (h, 16)
        } else if p.len() > 1 && p.starts_with('0') {
            (&p[1..], 8)
        } else {
            (*p, 10)
        };
        if digits.is_empty() && radix != 16 {
            return None;
        }
        let value = if digits.is_empty() { 0 } else { u64::from_str_radix(digits, radix).ok()? };
        values.push(value);
    }
    let (last, head) = values.split_last()?;
    if head.iter().any(|v| *v > 255) || *last >= 1u64 << (8 * (4 - head.len())) {
        return None;
    }
    let mut addr = 0u64;
    for (i, v) in head.iter().enumerate() {
        addr |= v << (24 - 8 * i);
    }
    Some((addr | last) as u32)
}

fn local_v4(ip: u32) -> bool {
    const NETS: [(u32, u32); 15] = [
        (0x0000_0000, 8), (0x0a00_0000, 8), (0x6440_0000, 10), (0x7f00_0000, 8), (0xa9fe_0000, 16),
        (0xac10_0000, 12), (0xc000_0000, 24), (0xc000_0200, 24), (0xc0a8_0000, 16), (0xc612_0000, 15),
        (0xc633_6400, 24), (0xcb00_7100, 24), (0xe000_0000, 4), (0xf000_0000, 4), (0xffff_ffff, 32),
    ];
    NETS.iter().any(|(net, bits)| ip >> (32 - bits) == net >> (32 - bits))
}

fn local_v6(ip: std::net::Ipv6Addr) -> bool {
    let a = u128::from(ip);
    let within = |net: u128, bits: u32| a >> (128 - bits) == net >> (128 - bits);
    !within(0x2000 << 112, 3) || within(0x2001 << 112, 23) || within(0x2001_0db8 << 96, 32) || within(0x2002 << 112, 16)
}

/// An http(s) URL the relay may fetch. Refuses blob:/data:/file:, credentials, whitespace and (unless `allow_local`,
/// which only tests set) local addresses.
pub fn validate_url(url: &Value, allow_local: bool) -> Result<String> {
    let invalid = || Error::rejected("invalid video link");
    let Some(url) = url.as_str().filter(|u| !u.is_empty() && u.chars().count() <= MAX_URL) else { return Err(invalid()) };
    let low: String = url.chars().take(8).collect::<String>().to_lowercase();
    if low.starts_with("blob:") {
        return Err(Error::rejected(
            "the page plays this video from a blob (in-page streaming); copy the page link or the video's own link instead",
        ));
    }
    if !(low.starts_with("http://") || low.starts_with("https://")) {
        return Err(Error::rejected("only http(s) video links can be played"));
    }
    if has_control(url) {
        return Err(invalid());
    }
    let netloc = split(url).netloc;
    if !netloc.is_ascii() || netloc.contains('[') != netloc.contains(']') {
        return Err(invalid());
    }
    if let Some((_, bracketed)) = netloc.split_once('[') {
        if parse_ipv6(bracketed.split_once(']').map_or(bracketed, |(h, _)| h)).is_none() {
            return Err(invalid());
        }
    }
    if netloc.contains('@') {
        return Err(Error::rejected("links with a user name or password are not played"));
    }
    let host = hostname(netloc).map(|h| h.trim_end_matches('.').to_owned()).unwrap_or_default();
    if host.is_empty() {
        return Err(invalid());
    }
    let port = port(netloc);
    if !port.is_empty() && !(port.bytes().all(|b| b.is_ascii_digit()) && port.parse::<u32>().is_ok_and(|p| p <= 65535)) {
        return Err(invalid());
    }
    if !allow_local {
        let local = host == "localhost"
            || host.ends_with(".localhost")
            || inet_aton(&host).is_some_and(local_v4)
            || parse_ipv6(&host).is_some_and(local_v6);
        if local {
            return Err(Error::rejected("local addresses are not played"));
        }
    }
    Ok(url.to_owned())
}

/// One checked source of a web request.
#[derive(Debug, Clone, PartialEq)]
pub struct Source {
    pub src: String,
    /// The MIME type, lowercased ('' if not given).
    pub mime: String,
    pub quality: String,
    pub format: Option<String>,
    pub via: &'static str,
}

/// A checked `{"web"}` request (or a bare `{"url"}` link in the same shape).
#[derive(Debug, Clone, PartialEq)]
pub struct Web {
    pub page: Option<String>,
    pub title: String,
    pub format: Option<String>,
    pub fov: Option<f64>,
    pub start: f64,
    pub ua: Option<String>,
    pub sources: Vec<Source>,
}

/// Python's `obj.get(MARKER) != 1`: 1, 1.0 and true all equal 1.
fn is_one(value: Option<&Value>) -> bool {
    match value {
        Some(Value::Bool(b)) => *b,
        Some(Value::Number(n)) => n.as_f64() == Some(1.0),
        _ => false,
    }
}

fn player_format(value: Option<&Value>) -> Result<Option<String>> {
    match value {
        None | Some(Value::Null) => Ok(None),
        Some(Value::String(s)) if token(s) => Ok(Some(s.to_uppercase())),
        Some(_) => Err(Error::rejected("invalid player format")),
    }
}

/// A `{"web"}` request object (version 1), normalised. Every refusal is a user-facing reason.
pub fn validate_web(obj: &Value, allow_local: bool) -> Result<Web> {
    let Value::Object(map) = obj else { return Err(Error::rejected("not an XR Workspace browser-video link")) };
    if !is_one(map.get(MARKER)) || map.keys().any(|k| !WEB_KEYS.contains(&k.as_str())) {
        return Err(Error::rejected("not an XR Workspace browser-video link"));
    }
    // The Referer is optional; a file:// or odd page is simply not sent.
    let page = map.get("page").and_then(Value::as_str).filter(|p| {
        let low = p.to_lowercase();
        p.chars().count() <= MAX_URL && (low.starts_with("http://") || low.starts_with("https://")) && !has_control(p)
    });
    let title = map.get("title").and_then(Value::as_str).map_or_else(String::new, |t| js::truncate_chars(t, MAX_WEB_TITLE));
    let format = player_format(map.get("format"))?;
    let fov = match map.get("fov") {
        None | Some(Value::Null) => None,
        Some(v) => match js::number(v) {
            Some(f) if (90.0..=360.0).contains(&f) => Some(f),
            _ => return Err(Error::rejected("invalid fisheye field of view")),
        },
    };
    let start = map.get("start").and_then(js::number).filter(|s| (0.0..=MAX_START_S).contains(s)).unwrap_or(0.0);
    let ua = printable(map.get("ua"), MAX_UA).filter(|u| !u.is_empty()).map(str::to_owned);
    let sources = match map.get("sources") {
        Some(Value::Array(s)) if !s.is_empty() => s,
        _ => return Err(Error::rejected("the page shows no video link (is a video player on it?)")),
    };
    let (mut clean, mut refusals) = (Vec::new(), Vec::new());
    for s in sources.iter().take(MAX_SOURCES) {
        let bad = || Error::rejected("invalid video source");
        let Value::Object(s) = s else { return Err(bad()) };
        if s.keys().any(|k| !SOURCE_KEYS.contains(&k.as_str())) {
            return Err(bad());
        }
        let src = match validate_url(s.get("src").unwrap_or(&Value::Null), allow_local) {
            Ok(src) => src,
            Err(e) => {
                refusals.push(e);
                continue;
            }
        };
        let text = |key, limit| match s.get(key) {
            None | Some(Value::Null) => Ok(String::new()),
            v => printable(v, limit).map(str::to_owned).ok_or_else(bad),
        };
        let (mime, quality) = (text("type", 64)?.to_lowercase(), text("quality", 32)?);
        let format = player_format(s.get("format"))?;
        let via = match s.get("via") {
            None => Some("dl8"),
            Some(v) => v.as_str().and_then(|v| VIA.iter().find(|x| **x == v).copied()),
        };
        let Some(via) = via else { return Err(bad()) };
        clean.push(Source { src, mime, quality, format, via });
    }
    if clean.is_empty() {
        return Err(refusals.into_iter().next().unwrap_or_else(|| Error::rejected("the page shows no playable video link")));
    }
    Ok(Web { page: page.map(str::to_owned), title, format, fov, start, ua, sources: clean })
}

/// Clipboard text -> the link for a `video_open` request (`{"url": ...}`). Refusals are shown to the user.
pub fn parse_clipboard(text: &str, allow_local: bool) -> Result<String> {
    let t = py_strip(text);
    if t.is_empty() {
        return Err(Error::rejected("the clipboard is empty: copy a video link first"));
    }
    if text.chars().count() > MAX_CLIPBOARD {
        return Err(Error::rejected("the clipboard holds too much text to be a video link"));
    }
    if t.contains('\n') || t.contains(' ') {
        return Err(Error::rejected("the clipboard does not hold a video link"));
    }
    validate_url(&Value::String(t.to_owned()), allow_local)
}

/// video_open's web forms (`{"url"}` or `{"web"}`) as one checked shape.
pub fn web_request(payload: &Map<String, Value>, allow_local: bool) -> Result<Web> {
    if let Some(url) = payload.get("url") {
        let url = validate_url(url, allow_local)?;
        return validate_web(&json!({MARKER: 1, "sources": [{"src": url, "via": "link"}]}), allow_local);
    }
    validate_web(payload.get("web").unwrap_or(&Value::Null), allow_local)
}

// --- formats and source choice ------------------------------------------------------------------------------------

/// Player format token -> our format, None if unknown (detect from the stream). Refuses flat video.
pub fn dl8_format(token: Option<&str>, fov: Option<f64>) -> Result<Option<Format>> {
    let Some(token) = token.filter(|t| !t.is_empty()).map(str::to_uppercase) else { return Ok(None) };
    if DL8_FLAT.contains(&token.as_str()) {
        return Err(Error::rejected("this is a flat (non-VR) video: watch it on a desktop instead"));
    }
    let Some((projection, stereo)) = dl8_known(&token) else { return Ok(None) };
    let fov = match fov {
        Some(f) if projection == Projection::Fisheye => f.clamp(180.0, 220.0).round_ties_even() as i64,
        _ => 180,
    };
    Ok(Some(Format::new(projection, stereo, fov)))
}

pub fn source_kind(source: &Source) -> &'static str {
    let path = split(&source.src).path.to_lowercase();
    if HLS_TYPES.contains(&source.mime.as_str()) || path.ends_with(".m3u8") {
        "hls"
    } else if DASH_TYPES.contains(&source.mime.as_str()) || path.ends_with(".mpd") {
        "dash"
    } else {
        "progressive"
    }
}

/// `(\d+(?:\.\d+)?)\s*k\b` at `i`: the number's end, if it matches there.
fn k_label(s: &[char], i: usize) -> Option<usize> {
    let digits = |from: usize| (from..s.len()).find(|&j| !s[j].is_ascii_digit()).unwrap_or(s.len());
    let int_end = digits(i);
    if int_end == i {
        return None;
    }
    let tail = |end: usize| {
        let mut q = end;
        while q < s.len() && py_space(s[q]) {
            q += 1;
        }
        (q < s.len() && s[q] == 'k' && s.get(q + 1).is_none_or(|c| !word(*c))).then_some(end)
    };
    if s.get(int_end) == Some(&'.') && s.get(int_end + 1).is_some_and(char::is_ascii_digit) {
        if let Some(end) = tail(digits(int_end + 1)) {
            return Some(end);
        }
    }
    tail(int_end)
}

/// `(\d{3,4})\s*[pi]?\b` at `i`: the digits' end, if it matches there.
fn p_label(s: &[char], i: usize) -> Option<usize> {
    let boundary = |x: usize| (x > 0 && word(s[x - 1])) != s.get(x).is_some_and(|c| word(*c));
    let run = (i..s.len()).take_while(|&j| s[j].is_ascii_digit()).count();
    for len in [4, 3] {
        if run < len {
            continue;
        }
        let p = i + len;
        let mut spaces = p;
        while spaces < s.len() && py_space(s[spaces]) {
            spaces += 1;
        }
        for q in (p..=spaces).rev() {
            if s.get(q).is_some_and(|c| *c == 'p' || *c == 'i') && boundary(q + 1) || boundary(q) {
                return Some(p);
            }
        }
    }
    None
}

/// '8K' -> 4320, '5.7k' -> 3078, '4k' -> 2160, '2160p' -> 2160, 'UHD' -> 2160; 0 if unknown.
pub fn quality_height(label: &str) -> i64 {
    let s = py_strip(label).to_lowercase();
    let chars: Vec<char> = s.chars().collect();
    for i in 0..chars.len() {
        if let Some(end) = k_label(&chars, i) {
            let number: String = chars[i..end].iter().collect();
            return number.parse::<f64>().map_or(0, |n| (n * 540.0) as i64);
        }
    }
    for i in 0..chars.len() {
        if let Some(end) = p_label(&chars, i) {
            return chars[i..end].iter().collect::<String>().parse().unwrap_or(0);
        }
    }
    match s.as_str() {
        "uhd" => 2160,
        "fhd" | "full hd" => 1080,
        "hd" => 720,
        "sd" => 480,
        _ => 0,
    }
}

/// The best source: the player's own over a page-wide find, then the highest quality label, then a progressive file
/// (seeks and reconnects best), then HLS/DASH (ffmpeg takes their best variant), then page order.
pub fn choose_source(web: &Web) -> &Source {
    let kind_rank = |s: &Source| ["progressive", "hls", "dash"].iter().position(|k| *k == source_kind(s));
    web.sources
        .iter()
        .enumerate()
        .min_by_key(|(i, s)| (VIA.iter().position(|v| *v == s.via), -quality_height(&s.quality), kind_rank(s), *i))
        .map(|(_, s)| s)
        .expect("validate_web keeps at least one source")
}

/// Python `unquote`: %XX escapes decoded as UTF-8 (invalid sequences become U+FFFD).
fn unquote(text: &str) -> String {
    let bytes = text.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        let hex = |b: u8| (b as char).to_digit(16);
        if bytes[i] == b'%' {
            if let (Some(h), Some(l)) = (bytes.get(i + 1).and_then(|b| hex(*b)), bytes.get(i + 2).and_then(|b| hex(*b))) {
                out.push((h * 16 + l) as u8);
                i += 3;
                continue;
            }
        }
        out.push(bytes[i]);
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

pub fn name_from_url(url: &str) -> String {
    let path = split(url).path;
    let name = unquote(path.rsplit('/').next().unwrap_or(""));
    if name.is_empty() { String::from("stream") } else { name }
}

pub fn web_title(web: &Web, source: &Source) -> String {
    let title = py_strip(&web.title);
    if title.is_empty() { name_from_url(&source.src) } else { title.to_owned() }
}

/// Request headers, in order.
pub type Headers = Vec<(String, String)>;

pub fn request_headers(web: &Web) -> Headers {
    web.page.iter().filter(|p| !p.is_empty()).map(|p| (String::from("Referer"), p.clone())).collect()
}

// --- failure reasons ----------------------------------------------------------------------------------------------

/// `(?i)\b(?:https?|ftp|file|blob|data)://\S+|\bwww\.\S+` replaced by `<link>`.
fn hide_links(line: &str) -> String {
    let s: Vec<char> = line.chars().collect();
    let lower: Vec<char> = s.iter().map(|c| c.to_ascii_lowercase()).collect();
    let at = |i: usize, word: &str| word.chars().enumerate().all(|(k, w)| lower.get(i + k) == Some(&w));
    let rest = |from: usize| (from..s.len()).find(|&j| py_space(s[j])).unwrap_or(s.len());
    let mut out = String::new();
    let mut i = 0;
    while i < s.len() {
        let boundary = (i > 0 && word(s[i - 1])) != word(s[i]);
        let mut end = None;
        if boundary {
            for scheme in ["https", "http", "ftp", "file", "blob", "data"] {
                let p = i + scheme.chars().count();
                if at(i, scheme) && at(p, "://") && s.get(p + 3).is_some_and(|c| !py_space(*c)) {
                    end = Some(rest(p + 3));
                    break;
                }
            }
            if end.is_none() && at(i, "www.") && s.get(i + 4).is_some_and(|c| !py_space(*c)) {
                end = Some(rest(i + 4));
            }
        }
        match end {
            Some(e) => {
                out.push_str("<link>");
                i = e;
            }
            None => {
                out.push(s[i]);
                i += 1;
            }
        }
    }
    out
}

/// `^(?:ERROR:\s*)?(?:\[[^\]]*\]\s*)*(?:[\w.-]{4,}:\s+)?` removed.
fn strip_prefix_noise(line: &str) -> &str {
    let skip_space = |t: &str| t.trim_start_matches(py_space).len();
    let mut rest = line;
    if let Some(r) = rest.strip_prefix("ERROR:") {
        rest = &r[r.len() - skip_space(r)..];
    }
    while let Some(inner) = rest.strip_prefix('[') {
        let Some(close) = inner.find(']') else { break };
        let after = &inner[close + 1..];
        rest = &after[after.len() - skip_space(after)..];
    }
    let run = rest.find(|c: char| !(word(c) || c == '.' || c == '-')).unwrap_or(rest.len());
    if rest[..run].chars().count() >= 4 {
        if let Some(after) = rest[run..].strip_prefix(':') {
            let spaces = after.len() - skip_space(after);
            if spaces > 0 {
                rest = &after[spaces..];
            }
        }
    }
    rest
}

/// The last meaningful stderr line, safe to show: no links, no `[https @ 0x...]` or `[site] <video id>:` prefix.
pub fn sanitize(text: &[u8], limit: usize) -> String {
    let text = String::from_utf8_lossy(text);
    for raw in splitlines(&text).into_iter().rev() {
        let hidden = hide_links(py_strip(raw));
        let mut line = strip_prefix_noise(&hidden);
        // ^(?:<link>\s*:?\s*)+
        while let Some(r) = line.strip_prefix("<link>") {
            let r = r.trim_start_matches(py_space);
            line = r.strip_prefix(':').unwrap_or(r).trim_start_matches(py_space);
        }
        let line = line.trim_matches([' ', ':', '.']);
        let mut clean = String::new();
        for c in line.chars().map(|c| if js::char_printable(c) { c } else { ' ' }) {
            if py_space(c) {
                if !clean.ends_with(' ') || clean.is_empty() {
                    clean.push(' ');
                }
            } else {
                clean.push(c);
            }
        }
        let low = clean.to_lowercase();
        if !clean.is_empty() && clean != "<link>" && !low.starts_with("conversion failed") && !low.starts_with("error opening input") {
            return if clean.chars().count() > limit {
                format!("{}...", js::truncate_chars(&clean, limit - 3).trim_end_matches(py_space))
            } else {
                clean
            };
        }
    }
    String::new()
}

/// `\b[45]xx\b` somewhere in `low`.
fn status_class(low: &str) -> bool {
    let s: Vec<char> = low.chars().collect();
    (0..s.len().saturating_sub(2)).any(|i| {
        matches!(s[i], '4' | '5')
            && s[i + 1] == 'x'
            && s[i + 2] == 'x'
            && (i == 0 || !word(s[i - 1]))
            && s.get(i + 3).is_none_or(|c| !word(*c))
    })
}

/// The first `http error NNN` (three digits) in `low`.
fn http_error_code(low: &str) -> Option<&str> {
    low.match_indices("http error ").find_map(|(i, m)| {
        let code = low.get(i + m.len()..i + m.len() + 3)?;
        code.bytes().all(|b| b.is_ascii_digit()).then_some(code)
    })
}

/// `invalid` (not a media file: often a web page), `http` or `other`.
pub fn failure_kind(stderr: &[u8]) -> &'static str {
    let low = String::from_utf8_lossy(stderr).to_lowercase();
    if low.contains("invalid data found") {
        "invalid"
    } else if http_error_code(&low).is_some()
        || low.match_indices("server returned ").any(|(i, m)| low[i + m.len()..].starts_with(|c: char| c.is_ascii_digit()))
        || status_class(&low)
    {
        "http"
    } else {
        "other"
    }
}

/// ffmpeg/ffprobe stderr -> a short reason (never the URL, which may hold a signed token). Unknown errors show the
/// tool's own last line (links removed), not just an exit code.
pub fn explain_failure(stderr: &[u8], returncode: Option<i32>, tool: &str) -> String {
    let text = String::from_utf8_lossy(stderr);
    let low = text.to_lowercase();
    if low.contains("cenc") || low.contains("drm") || low.contains("widevine") || (low.contains("encrypt") && !low.contains("aes")) {
        return String::from("the stream is DRM-protected and plays only inside the browser");
    }
    let known = [
        ("401", "the site wants a login for this stream (HTTP 401)"),
        ("403", "the site refused the stream (HTTP 403): the link may have expired or need your browser login"),
        ("404", "the stream was not found (HTTP 404): the link may have expired"),
        ("410", "the stream is gone (HTTP 410)"),
        ("429", "the site is rate-limiting requests (HTTP 429): try again in a minute"),
    ];
    for (code, why) in known {
        if low.contains(&format!("http error {code}")) || low.contains(&format!("server returned {code}")) {
            return why.into();
        }
    }
    if let Some(code) = http_error_code(&low) {
        return format!("the site answered HTTP {code}");
    }
    let table = [
        ("server returned 4xx", "the site refused the request (HTTP 4xx)"),
        ("server returned 5xx", "the site had an error (HTTP 5xx): try again later"),
        ("not on whitelist", "the stream refers to something other than web links, which is not played"),
        ("timed out", "the site did not answer in time"),
        ("name or service not known", "could not look up the site (offline?)"),
        ("failed to resolve", "could not look up the site (offline?)"),
        ("temporary failure in name", "could not look up the site (offline?)"),
        ("connection refused", "the site refused the connection"),
        ("invalid data found", "the link does not lead to a playable video"),
    ];
    if let Some((_, why)) = table.iter().find(|(needle, _)| low.contains(needle)) {
        return (*why).into();
    }
    let detail = sanitize(stderr, 140);
    if !detail.is_empty() {
        return format!("{tool} could not open the stream: {detail}");
    }
    match returncode {
        Some(rc) if rc != 0 => format!("the stream could not be opened ({tool} exit {rc})"),
        _ => String::from("the stream could not be opened"),
    }
}

// --- probe and relay ----------------------------------------------------------------------------------------------

pub fn ffmpeg_path() -> Option<String> {
    process::find_tool("ffmpeg").map(|p| p.to_string_lossy().into_owned())
}

fn net_args(headers: &[(String, String)], ua: Option<&str>) -> Vec<String> {
    let mut args: Vec<String> = ["-protocol_whitelist", NET_PROTOCOLS, "-rw_timeout", "15000000"].map(String::from).into();
    if let Some(ua) = ua.filter(|u| !u.is_empty()) {
        args.extend([String::from("-user_agent"), ua.to_owned()]);
    }
    if !headers.is_empty() {
        args.push(String::from("-headers"));
        args.push(headers.iter().map(|(k, v)| format!("{k}: {v}\r\n")).collect());
    }
    args
}

/// ffprobe the stream over the network (same protocols as the relay). None if ffprobe is missing. HLS/DASH: the
/// highest-resolution video stream is reported (the one the relay picks).
pub fn probe_url(url: &str, headers: &[(String, String)], ua: Option<&str>, run: Runner, ffprobe: Option<&str>) -> Result<Option<ProbeInfo>> {
    let exe = match ffprobe {
        Some(e) => Some(e.to_owned()),
        None => video::ffprobe_path(),
    };
    let Some(exe) = exe.filter(|e| !e.is_empty()) else { return Ok(None) };
    let mut argv = vec![exe, "-v".into(), "error".into(), "-hide_banner".into()];
    argv.extend(net_args(headers, ua));
    argv.extend(["-print_format", "json", "-show_streams", "-show_format", "-i", url].map(String::from));
    let inv = Invocation { argv, env: process::tool_env(), pass_fd: None, timeout: WEB_PROBE_TIMEOUT, max_output: video::FFPROBE_MAX_OUTPUT };
    let out = match run(&inv) {
        Ok(out) => out,
        Err(RunError::Timeout) => return Err(Error::rejected("the site took too long to answer")),
        Err(RunError::Spawn(_)) => return Ok(None),
    };
    if out.returncode != 0 || out.stdout_over {
        return Err(Error::ProbeFailed {
            message: explain_failure(&out.stderr, Some(out.returncode), "ffprobe"),
            kind: failure_kind(&out.stderr),
        });
    }
    let mut data: Value = serde_json::from_slice(&out.stdout).map_err(|_| Error::rejected("the link does not lead to a playable video"))?;
    if let Some(Value::Array(streams)) = data.get_mut("streams") {
        // Variant streams (HLS/DASH): report the one ffmpeg's default selection takes, the largest picture.
        let area = |s: &Value| match (s.get("width").and_then(js::int), s.get("height").and_then(js::int)) {
            (Some(w), Some(h)) => i128::from(w) * i128::from(h),
            _ => 0,
        };
        let videos: Vec<Value> = streams.iter().filter(|s| video::is_video_stream(s)).cloned().collect();
        let mut best: Option<&Value> = None;
        for v in &videos {
            if best.is_none_or(|b| area(v) > area(b)) {
                best = Some(v);
            }
        }
        let mut reordered: Vec<Value> = best.into_iter().cloned().collect();
        reordered.extend(streams.iter().filter(|s| !videos.contains(s)).cloned());
        *streams = reordered;
    }
    let mut info = video::parse_probe(&data)?;
    let name = data.get("format").and_then(|f| f.get("format_name")).filter(|n| js::truthy(n)).map_or_else(String::new, js::text);
    info.container = Some(js::truncate_chars(&name, 64));
    Ok(Some(info))
}

/// ffmpeg copying the stream (video + audio, no re-encoding) as NUT to stdout. NUT carries every codec the renderer
/// plays, streams without an index, and starts cleanly mid-way in HLS. `audio`: a separate audio stream (sites that
/// list video and audio apart, resolved by yt-dlp); both inputs get the same headers and start.
pub fn relay_argv(url: &str, headers: &[(String, String)], ua: Option<&str>, start: f64, ffmpeg: Option<&str>, audio: Option<&str>) -> Result<Vec<String>> {
    let exe = ffmpeg.filter(|e| !e.is_empty()).map(str::to_owned).or_else(ffmpeg_path);
    let Some(exe) = exe else { return Err(Error::rejected("ffmpeg is not installed")) };
    let mut argv: Vec<String> = vec![exe, "-nostdin".into(), "-hide_banner".into(), "-loglevel".into(), "error".into()];
    for u in std::iter::once(url).chain(audio) {
        argv.extend(net_args(headers, ua));
        // http: resume a dropped link.
        argv.extend(["-reconnect", "1", "-reconnect_on_network_error", "1", "-reconnect_delay_max", "5"].map(String::from));
        if start > 0.0 {
            argv.extend([String::from("-ss"), format!("{start:.3}")]);
        }
        argv.extend([String::from("-i"), u.to_owned()]);
    }
    if audio.is_some() {
        argv.extend(["-map", "0:v:0", "-map", "1:a:0"].map(String::from));
    }
    argv.extend(["-sn", "-dn", "-c", "copy", "-f", "nut", "pipe:1"].map(String::from));
    Ok(argv)
}

/// Best effort: the default size still works.
fn grow_pipe(fd: RawFd) {
    // SAFETY: fcntl on a descriptor number has no memory effects.
    unsafe { libc::fcntl(fd, libc::F_SETPIPE_SZ, RELAY_PIPE) };
}

fn pipe(flags: i32) -> io::Result<(OwnedFd, OwnedFd)> {
    let mut fds = [0; 2];
    // SAFETY: `fds` has room for the two descriptors pipe2 writes.
    if unsafe { libc::pipe2(fds.as_mut_ptr(), flags) } < 0 {
        return Err(io::Error::last_os_error());
    }
    // SAFETY: both descriptors were just created and are owned by nothing else.
    Ok(unsafe { (OwnedFd::from_raw_fd(fds[0]), OwnedFd::from_raw_fd(fds[1])) })
}

fn set_nonblocking(fd: RawFd) -> io::Result<()> {
    // SAFETY: fcntl on a descriptor number has no memory effects.
    unsafe {
        let fl = libc::fcntl(fd, libc::F_GETFL);
        if fl < 0 || libc::fcntl(fd, libc::F_SETFL, fl | libc::O_NONBLOCK) < 0 {
            return Err(io::Error::last_os_error());
        }
    }
    Ok(())
}

/// poll() one or more descriptors; the `revents` of each, empty on timeout or error.
fn poll(fds: &[(RawFd, i16)], timeout_ms: i32) -> Vec<i16> {
    let mut set: Vec<libc::pollfd> = fds.iter().map(|(fd, events)| libc::pollfd { fd: *fd, events: *events, revents: 0 }).collect();
    // SAFETY: `set` is a valid array of `set.len()` pollfd entries.
    let n = unsafe { libc::poll(set.as_mut_ptr(), set.len() as libc::nfds_t, timeout_ms) };
    if n <= 0 { vec![0; fds.len()] } else { set.iter().map(|p| p.revents).collect() }
}

/// What a WebStream relays through: the real StreamRelay, or a test stand-in.
pub trait Relay: Send {
    /// Start; the read end for the renderer.
    fn start(&mut self) -> io::Result<OwnedFd>;
    fn stop(&mut self);
    /// A reason if ffmpeg ended with an error, else None.
    fn failure(&self) -> Option<String>;
}

pub type RelayFactory = Box<dyn Fn(Vec<String>) -> Box<dyn Relay> + Send>;

pub fn stream_relay_factory() -> RelayFactory {
    Box::new(|argv| Box::new(StreamRelay::new(argv, RELAY_BUFFER)))
}

#[derive(Default)]
struct Buffer {
    chunks: VecDeque<Vec<u8>>,
    /// Bytes read from ffmpeg and not yet written to the renderer.
    size: usize,
    eof: bool,
}

#[derive(Default)]
struct Shared {
    buffer: Mutex<Buffer>,
    cond: Condvar,
    stop: AtomicBool,
    returncode: Mutex<Option<i32>>,
    stderr_tail: Mutex<Vec<u8>>,
    bytes_in: AtomicU64,
    bytes_out: AtomicU64,
}

/// One ffmpeg relay: network -> ffmpeg (copy) -> read-ahead buffer -> pipe whose read end goes to the renderer.
///
/// `start()` returns the read end. `stop()` ends ffmpeg and the pump in a few milliseconds (it runs on the
/// supervisor loop, once per seek). Threads: a reader (ffmpeg stdout/stderr into the buffer, bounded) and a writer
/// (buffer into the renderer's pipe, non-blocking so stop() never waits on a paused renderer); both also watch a
/// wake-up pipe that stop() writes to.
pub struct StreamRelay {
    argv: Vec<String>,
    limit: usize,
    child: Option<Arc<Mutex<Child>>>,
    pid: Option<i32>,
    shared: Arc<Shared>,
    threads: Vec<JoinHandle<()>>,
    wake: Option<(OwnedFd, OwnedFd)>,
    stdout_fd: Option<RawFd>,
}

impl StreamRelay {
    pub fn new(argv: Vec<String>, buffer: usize) -> StreamRelay {
        StreamRelay { argv, limit: buffer, child: None, pid: None, shared: Arc::default(), threads: Vec::new(), wake: None, stdout_fd: None }
    }

    pub fn returncode(&self) -> Option<i32> {
        *self.shared.returncode.lock().unwrap()
    }

    pub fn finished(&self) -> bool {
        self.returncode().is_some()
    }

    pub fn bytes_in(&self) -> u64 {
        self.shared.bytes_in.load(Ordering::Relaxed)
    }

    pub fn bytes_out(&self) -> u64 {
        self.shared.bytes_out.load(Ordering::Relaxed)
    }

    /// Bytes held between ffmpeg and the renderer.
    pub fn buffered(&self) -> usize {
        self.shared.buffer.lock().unwrap().size
    }

    pub fn limit(&self) -> usize {
        self.limit
    }

    /// ffmpeg's stdout pipe (tests read its size).
    pub fn stdout_fd(&self) -> Option<RawFd> {
        self.stdout_fd
    }

    fn spawn(&mut self) -> io::Result<OwnedFd> {
        let (read_end, write_end) = pipe(libc::O_CLOEXEC)?;
        set_nonblocking(write_end.as_raw_fd())?;
        grow_pipe(write_end.as_raw_fd());
        let wake = pipe(libc::O_CLOEXEC | libc::O_NONBLOCK)?;
        let (exe, args) = self.argv.split_first().ok_or_else(|| io::Error::from(io::ErrorKind::InvalidInput))?;
        let mut cmd = Command::new(exe);
        cmd.args(args).env_clear().envs(process::tool_env());
        cmd.stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::piped());
        // SAFETY: setsid is async-signal-safe; the new session lets stop() kill ffmpeg and anything it started.
        unsafe {
            cmd.pre_exec(|| if libc::setsid() < 0 { Err(io::Error::last_os_error()) } else { Ok(()) });
        }
        let mut child = cmd.spawn()?;
        let out = child.stdout.take().expect("piped stdout");
        let err = child.stderr.take().expect("piped stderr");
        grow_pipe(out.as_raw_fd());
        self.stdout_fd = Some(out.as_raw_fd());
        self.pid = Some(child.id() as i32);
        let child = Arc::new(Mutex::new(child));
        self.child = Some(child.clone());
        let wake_read = wake.0.as_raw_fd();
        self.wake = Some(wake);
        let (shared, limit) = (self.shared.clone(), self.limit);
        let pid = self.pid;
        self.threads.push(std::thread::spawn(move || read_loop(&shared, limit, OwnedFd::from(out), OwnedFd::from(err), wake_read, &child, pid)));
        let shared = self.shared.clone();
        self.threads.push(std::thread::spawn(move || write_loop(&shared, write_end, wake_read)));
        Ok(read_end)
    }

    fn kill(&self) {
        let Some(child) = &self.child else { return };
        if child.lock().unwrap().try_wait().ok().flatten().is_some() {
            return;
        }
        if let Some(pid) = self.pid {
            // SAFETY: killpg with a process group id has no memory effects.
            unsafe { libc::killpg(pid, libc::SIGKILL) };
        }
    }
}

fn exit_code(status: std::process::ExitStatus) -> i32 {
    status.code().unwrap_or_else(|| -status.signal().unwrap_or(0))
}

fn wait_for(child: &Mutex<Child>, timeout: Duration) -> Option<i32> {
    let deadline = Instant::now() + timeout;
    loop {
        if let Ok(Some(status)) = child.lock().unwrap().try_wait() {
            return Some(exit_code(status));
        }
        if Instant::now() >= deadline {
            return None;
        }
        std::thread::sleep(Duration::from_millis(5));
    }
}

fn read_loop(shared: &Shared, limit: usize, out: OwnedFd, err: OwnedFd, wake: RawFd, child: &Mutex<Child>, pid: Option<i32>) {
    let mut open = vec![(out.as_raw_fd(), false), (err.as_raw_fd(), true)];
    let mut buf = vec![0u8; RELAY_CHUNK];
    while !open.is_empty() && !shared.stop.load(Ordering::SeqCst) {
        {
            let mut b = shared.buffer.lock().unwrap();
            while b.size >= limit && !shared.stop.load(Ordering::SeqCst) {
                b = shared.cond.wait_timeout(b, Duration::from_millis(250)).unwrap().0;
            }
        }
        let mut fds: Vec<(RawFd, i16)> = open.iter().map(|(fd, _)| (*fd, libc::POLLIN)).collect();
        fds.push((wake, libc::POLLIN));
        let ready = poll(&fds, 250);
        let mut closed = Vec::new();
        for (k, (fd, is_err)) in open.iter().enumerate() {
            if ready[k] == 0 {
                continue;
            }
            // SAFETY: `buf` is valid for `buf.len()` bytes and `fd` is open (owned by `out` or `err`).
            let n = unsafe { libc::read(*fd, buf.as_mut_ptr().cast(), buf.len()) };
            if n < 0 {
                if io::Error::last_os_error().kind() == io::ErrorKind::Interrupted {
                    continue;
                }
                closed.push(*fd);
            } else if n == 0 {
                closed.push(*fd);
            } else if *is_err {
                let mut tail = shared.stderr_tail.lock().unwrap();
                tail.extend_from_slice(&buf[..n as usize]);
                let excess = tail.len().saturating_sub(4096);
                tail.drain(..excess);
            } else {
                let mut b = shared.buffer.lock().unwrap();
                b.chunks.push_back(buf[..n as usize].to_vec());
                b.size += n as usize;
                shared.bytes_in.fetch_add(n as u64, Ordering::Relaxed);
                shared.cond.notify_all();
            }
        }
        open.retain(|(fd, _)| !closed.contains(fd));
    }
    drop((out, err));
    let code = wait_for(child, Duration::from_secs(5)).or_else(|| {
        if let Some(pid) = pid {
            // SAFETY: killpg with a process group id has no memory effects.
            unsafe { libc::killpg(pid, libc::SIGKILL) };
        }
        child.lock().unwrap().wait().ok().map(exit_code)
    });
    *shared.returncode.lock().unwrap() = code;
    shared.buffer.lock().unwrap().eof = true;
    shared.cond.notify_all();
}

fn write_loop(shared: &Shared, fd: OwnedFd, wake: RawFd) {
    let mut current: Vec<u8> = Vec::new();
    let mut offset = 0;
    while !shared.stop.load(Ordering::SeqCst) {
        if offset == current.len() {
            let mut b = shared.buffer.lock().unwrap();
            while b.chunks.is_empty() && !b.eof && !shared.stop.load(Ordering::SeqCst) {
                b = shared.cond.wait_timeout(b, Duration::from_millis(250)).unwrap().0;
            }
            if shared.stop.load(Ordering::SeqCst) {
                break;
            }
            match b.chunks.pop_front() {
                Some(chunk) => (current, offset) = (chunk, 0),
                None => break, // the end of ffmpeg's output, all written
            }
        }
        let ready = poll(&[(wake, libc::POLLIN), (fd.as_raw_fd(), libc::POLLOUT)], 250);
        if ready[1] == 0 {
            continue;
        }
        let rest = &current[offset..];
        // SAFETY: `rest` is valid for `rest.len()` bytes and `fd` is owned here.
        let n = unsafe { libc::write(fd.as_raw_fd(), rest.as_ptr().cast(), rest.len()) };
        if n < 0 {
            match io::Error::last_os_error().kind() {
                io::ErrorKind::WouldBlock | io::ErrorKind::Interrupted => continue,
                _ => break, // EPIPE: the renderer closed the video
            }
        }
        offset += n as usize;
        let mut b = shared.buffer.lock().unwrap();
        b.size -= n as usize;
        shared.bytes_out.fetch_add(n as u64, Ordering::Relaxed);
        shared.cond.notify_all();
    }
}

impl Relay for StreamRelay {
    fn start(&mut self) -> io::Result<OwnedFd> {
        self.spawn()
    }

    fn stop(&mut self) {
        let timeout = Duration::from_secs(3);
        self.shared.stop.store(true, Ordering::SeqCst);
        self.shared.cond.notify_all();
        if let Some((_, w)) = &self.wake {
            // SAFETY: writes one byte from a valid buffer to the wake-up pipe.
            unsafe { libc::write(w.as_raw_fd(), b"x".as_ptr().cast(), 1) };
        }
        // Killed at once: its output is being thrown away, and a copy into a pipe has nothing to finish. A SIGTERM'd
        // ffmpeg stays blocked writing to the pipe nobody reads any more.
        self.kill();
        let deadline = Instant::now() + timeout;
        while self.threads.iter().any(|t| !t.is_finished()) && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(2));
        }
        if self.threads.iter().all(JoinHandle::is_finished) {
            for t in self.threads.drain(..) {
                let _ = t.join();
            }
            self.wake = None;
        }
        if let Some(child) = &self.child {
            if self.returncode().is_none() {
                if let Some(code) = wait_for(child, Duration::from_secs(1)) {
                    *self.shared.returncode.lock().unwrap() = Some(code);
                }
            }
        }
    }

    fn failure(&self) -> Option<String> {
        match self.returncode() {
            Some(rc) if rc != 0 && !self.shared.stop.load(Ordering::SeqCst) => {
                Some(explain_failure(&self.shared.stderr_tail.lock().unwrap(), Some(rc), "ffmpeg"))
            }
            _ => None,
        }
    }
}

impl Drop for StreamRelay {
    fn drop(&mut self) {
        if self.child.is_some() && !self.shared.stop.load(Ordering::SeqCst) {
            Relay::stop(self);
        }
    }
}

/// What to relay for a web video.
#[derive(Debug, Clone, PartialEq)]
pub struct Target {
    pub url: String,
    pub audio: Option<String>,
    pub headers: Headers,
    pub ua: Option<String>,
    /// `progressive`, `hls` or `dash`.
    pub kind: &'static str,
    pub site: Option<String>,
    /// `direct` (a stream link) or `yt-dlp` (a page link).
    pub via: &'static str,
    pub title: String,
    /// Words that may name the VR format (detect_format reads them like a file name).
    pub hint: String,
}

/// Python's `round(x, 1)`.
fn round1(x: f64) -> f64 {
    format!("{x:.1}").parse().unwrap_or(x)
}

/// The playing web stream: what to relay, and a position estimate (renderer playing time since the last restart).
pub struct WebStream {
    pub target: Target,
    pub info: Option<ProbeInfo>,
    pub duration: Option<f64>,
    ffmpeg: Option<String>,
    factory: RelayFactory,
    relay: Option<Box<dyn Relay>>,
    /// Stream position at the relay's start.
    pub base: f64,
    /// Renderer 'playing' seconds since then.
    pub played: f64,
    pub pending_seek: Option<f64>,
    seek_at: Option<f64>,
    pub restarts: u32,
}

impl WebStream {
    pub fn new(target: Target, info: Option<ProbeInfo>, factory: RelayFactory, ffmpeg: Option<String>) -> WebStream {
        let duration = info.as_ref().and_then(|i| i.duration).filter(|d| *d > 0.0);
        WebStream { target, info, duration, ffmpeg, factory, relay: None, base: 0.0, played: 0.0, pending_seek: None, seek_at: None, restarts: 0 }
    }

    pub fn seekable(&self) -> bool {
        self.duration.is_some()
    }

    pub fn position(&self) -> f64 {
        let p = self.base + self.played;
        self.duration.map_or(p, |d| p.min(d))
    }

    /// (Re)start the relay at `at` seconds; the renderer's read descriptor.
    pub fn start(&mut self, at: f64) -> Result<OwnedFd> {
        if let Some(mut relay) = self.relay.take() {
            relay.stop();
        }
        let at = self.duration.map_or(at, |d| at.min(d - END_MARGIN_S).max(0.0)).max(0.0);
        let t = &self.target;
        let argv = relay_argv(&t.url, &t.headers, t.ua.as_deref(), at, self.ffmpeg.as_deref(), t.audio.as_deref())?;
        let relay = self.relay.insert((self.factory)(argv));
        let fd = relay.start().map_err(|e| Error::Rejected(e.to_string()))?;
        (self.base, self.played, self.restarts) = (at, 0.0, self.restarts + 1);
        Ok(fd)
    }

    pub fn advance(&mut self, dt: f64, state: &str) {
        if state == "playing" && dt > 0.0 {
            self.played += dt.min(1.0); // bounded: a stalled loop does not jump ahead
        }
    }

    pub fn request_seek(&mut self, delta: f64, now: f64) -> bool {
        if !self.seekable() {
            return false;
        }
        let target = self.pending_seek.unwrap_or_else(|| self.position()) + delta;
        (self.pending_seek, self.seek_at) = (Some(target.max(0.0)), Some(now));
        true
    }

    /// The combined seek target once the keys have settled, else None.
    pub fn due_seek(&mut self, now: f64) -> Option<f64> {
        let (target, at) = (self.pending_seek?, self.seek_at?);
        if now - at < SEEK_SETTLE_S {
            return None;
        }
        (self.pending_seek, self.seek_at) = (None, None);
        Some(target)
    }

    pub fn stop(&mut self) {
        if let Some(mut relay) = self.relay.take() {
            relay.stop();
        }
    }

    pub fn failure(&self) -> Option<String> {
        self.relay.as_ref().and_then(|r| r.failure())
    }

    /// The run's status entry (never the link).
    pub fn snapshot(&self) -> String {
        let t = &self.target;
        js::object(&[
            ("kind", js::string(t.kind)),
            ("site", t.site.as_deref().map_or_else(|| String::from("null"), js::string)),
            ("resolved", (t.via == "yt-dlp").to_string()),
            ("position", js::python_float(round1(self.position()))),
            ("duration", self.duration.map_or_else(|| String::from("null"), |d| js::python_float(round1(d)))),
            ("seekable", self.seekable().to_string()),
            ("seeking", self.pending_seek.is_some().to_string()),
        ])
    }
}

impl Drop for WebStream {
    fn drop(&mut self) {
        self.stop();
    }
}

// --- video pages: yt-dlp ------------------------------------------------------------------------------------------

/// A stream link (by its type or file extension), as opposed to a page to resolve.
pub fn looks_like_media(source: &Source) -> bool {
    let t = source.mime.as_str();
    if t.starts_with("video/") || t.starts_with("audio/") || HLS_TYPES.contains(&t) || DASH_TYPES.contains(&t) {
        return true;
    }
    let path = split(&source.src).path.to_lowercase();
    MEDIA_EXT.iter().any(|e| path.ends_with(e))
}

pub fn ytdlp_path() -> Option<String> {
    process::find_tool("yt-dlp").map(|p| p.to_string_lossy().into_owned())
}

/// No config files, plugins, cache, cookies or downloads; one video (no playlists); JSON on stdout.
pub fn ytdlp_argv(url: &str, exe: &str) -> Vec<String> {
    [
        exe, "--ignore-config", "--no-plugin-dirs", "--no-cache-dir", "--no-cookies", "--no-cookies-from-browser",
        "--no-playlist", "--skip-download", "--no-warnings", "--no-progress", "--socket-timeout", "15",
        "--dump-single-json", "--", url,
    ]
    .map(String::from)
    .into()
}

fn no_ytdlp() -> Error {
    Error::Rejected(format!("{PAGE_LINK}, and {NO_YTDLP}; {PAGE_HELP}"))
}

/// yt-dlp's info for a video page. Refusals: yt-dlp missing (NO_YTDLP in the text), PageUnsupported (no video found
/// or unknown site), or yt-dlp's reason (private, removed, age-gated...).
pub fn resolve_page(url: &str, run: Runner, ytdlp: Option<&str>) -> Result<Value> {
    let exe = match ytdlp {
        Some(e) => Some(e.to_owned()),
        None => ytdlp_path(),
    };
    let Some(exe) = exe.filter(|e| !e.is_empty()) else { return Err(no_ytdlp()) };
    let mut env = vec![(String::from("PATH"), String::from("/usr/bin:/bin")), (String::from("LC_ALL"), String::from("C.UTF-8"))];
    // Its JS runtime (deno) wants a home.
    if let Some(home) = std::env::var("HOME").ok().filter(|h| h.starts_with('/')) {
        env.push((String::from("HOME"), home));
    }
    let inv = Invocation { argv: ytdlp_argv(url, &exe), env, pass_fd: None, timeout: YTDLP_TIMEOUT, max_output: YTDLP_MAX_OUTPUT };
    let out = match run(&inv) {
        Ok(out) => out,
        Err(RunError::Timeout) => return Err(Error::rejected("yt-dlp took too long to read the page")),
        Err(RunError::Spawn(_)) => return Err(no_ytdlp()),
    };
    let low = String::from_utf8_lossy(&out.stderr).to_lowercase();
    if out.returncode != 0 || out.stdout_over {
        if low.contains("unsupported url") || low.contains("no video formats found") || low.contains("no video could be found") {
            return Err(Error::PageUnsupported(String::from("no video found on that page")));
        }
        if low.contains("drm") {
            return Err(Error::rejected("the video is DRM-protected and plays only inside the browser"));
        }
        let detail = sanitize(&out.stderr, 140);
        let tail = if detail.is_empty() { String::new() } else { format!(": {detail}") };
        return Err(Error::Rejected(format!("yt-dlp could not get the video{tail}")));
    }
    let unreadable = || Error::rejected("yt-dlp gave no readable answer");
    let data: Value = serde_json::from_slice(&out.stdout).map_err(|_| unreadable())?;
    if !data.is_object() {
        return Err(unreadable());
    }
    if data.get("_type").and_then(Value::as_str) == Some("playlist") {
        let entries = data.get("entries").and_then(Value::as_array).map_or(&[][..], Vec::as_slice);
        return entries.iter().find(|e| e.is_object()).cloned().ok_or_else(|| Error::PageUnsupported(String::from("no video found on that page")));
    }
    Ok(data)
}

fn codec(vcodec: Option<&Value>) -> String {
    let v = vcodec.filter(|v| js::truthy(v)).map_or_else(String::new, js::text).to_lowercase();
    let prefixes = [
        ("avc", "h264"), ("h264", "h264"), ("hev", "hevc"), ("hvc", "hevc"), ("h265", "hevc"), ("vp09", "vp9"),
        ("vp9", "vp9"), ("av01", "av1"), ("av1", "av1"),
    ];
    if let Some((_, name)) = prefixes.iter().find(|(p, _)| v.starts_with(p)) {
        return (*name).into();
    }
    let head = v.split('.').next().unwrap_or("");
    if head.is_empty() { String::from("unknown") } else { head.to_owned() }
}

/// A number (not NaN), else 0.
fn num(value: Option<&Value>) -> f64 {
    value.and_then(js::number).filter(|n| !n.is_nan()).unwrap_or(0.0)
}

fn is_str(value: Option<&Value>, text: &str) -> bool {
    value.and_then(Value::as_str) == Some(text)
}

fn usable(f: &Value) -> bool {
    f.is_object()
        && f.get("url").is_some_and(Value::is_string)
        && f.get("protocol").map_or(Some("https"), Value::as_str).is_some_and(|p| RELAY_PROTOCOLS.contains(&p))
        && !f.get("has_drm").is_some_and(js::truthy)
        && !f.get("fragments").is_some_and(js::truthy)
}

/// The first item with the greatest rank (Python's `max`).
fn first_max<'a, const N: usize>(items: impl Iterator<Item = &'a Value>, rank: impl Fn(&Value) -> [f64; N]) -> Option<&'a Value> {
    let mut best: Option<(&Value, [f64; N])> = None;
    for item in items {
        let r = rank(item);
        if best.as_ref().is_none_or(|(_, b)| r.as_slice() > b.as_slice()) {
            best = Some((item, r));
        }
    }
    best.map(|(item, _)| item)
}

/// (video format, separate audio format or None) from yt-dlp's list. Best = decodes in hardware here at its size,
/// then the most pixels, then frame rate, then bitrate (for VR, resolution is the quality limit).
pub fn pick_formats(data: &Value) -> Result<(&Value, Option<&Value>)> {
    let listed: &[Value] = match data.get("formats") {
        Some(f) if js::truthy(f) => f.as_array().map_or(&[], Vec::as_slice),
        _ => std::slice::from_ref(data),
    };
    let formats: Vec<&Value> = listed.iter().filter(|f| usable(f)).collect();
    // vcodec 'none' is audio only; a missing one means unknown (yt-dlp's generic extractor on a plain <video>).
    let videos: Vec<&Value> = formats
        .iter()
        .copied()
        .filter(|f| !is_str(f.get("vcodec"), "none"))
        .filter(|f| num(f.get("width")).max(num(f.get("height"))) <= video::MAX_SIDE as f64)
        .collect();
    let rank = |f: &Value| {
        let (w, h) = (num(f.get("width")), num(f.get("height")));
        let hw = video::hw_decode_max_width(&codec(f.get("vcodec"))).is_some_and(|limit| w <= limit as f64);
        let rate = match num(f.get("tbr")) {
            0.0 => num(f.get("vbr")),
            t => t,
        };
        [f64::from(u8::from(hw)), f64::from(u8::from(f.get("vcodec").is_some_and(js::truthy))), w * h, num(f.get("fps")), rate]
    };
    let Some(video) = first_max(videos.iter().copied(), rank) else {
        let all = data.get("formats").and_then(Value::as_array).map_or(&[][..], Vec::as_slice);
        if all.iter().any(|f| f.get("has_drm").is_some_and(js::truthy)) {
            return Err(Error::rejected("the video is DRM-protected and plays only inside the browser"));
        }
        return Err(Error::rejected("yt-dlp found no stream that can be played outside the browser"));
    };
    if !is_str(video.get("acodec"), "none") {
        return Ok((video, None)); // has (or may have) its own audio
    }
    let audios = formats
        .iter()
        .copied()
        .filter(|f| is_str(f.get("vcodec"), "none") && f.get("acodec").is_some_and(|a| !a.is_null() && a.as_str() != Some("none")));
    let audio = first_max(audios, |f| {
        let rate = match num(f.get("abr")) {
            0.0 => num(f.get("tbr")),
            a => a,
        };
        [rate, num(f.get("asr"))]
    });
    Ok((video, audio))
}

/// yt-dlp's per-format request headers, cookie-free and printable ASCII only: (headers, user agent).
pub fn relay_headers(fmt: &Value) -> (Headers, Option<String>) {
    let (mut headers, mut ua) = (Vec::new(), None);
    let Some(map) = fmt.get("http_headers").and_then(Value::as_object) else { return (headers, ua) };
    for (k, v) in map {
        let name_ok = (1..=64).contains(&k.len()) && k.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-');
        let Some(v) = printable(Some(v), 1024).filter(|v| !v.is_empty() && name_ok) else { continue };
        let low = k.to_lowercase();
        if DROP_HEADERS.contains(&low.as_str()) {
            continue;
        }
        if low == "user-agent" {
            ua = Some(js::truncate_chars(v, MAX_UA));
        } else if headers.len() < 12 {
            headers.push((k.clone(), v.to_owned()));
        }
    }
    (headers, ua)
}

/// Words that may name the VR format: the title and tags (detect_format reads tokens like 360, VR180, SBS, TB).
pub fn vr_hint(data: &Value) -> String {
    let mut words = vec![data.get("title").filter(|t| js::truthy(t)).map_or_else(String::new, js::text)];
    if let Some(tags) = data.get("tags").and_then(Value::as_array) {
        words.extend(tags.iter().take(40).filter_map(Value::as_str).map(str::to_owned));
    }
    js::truncate_chars(&words.join(" "), 2000)
}

/// probe_url or a stand-in: (link, headers, user agent) -> what ffprobe says.
pub type UrlProbe<'a> = &'a dyn Fn(&str, &[(String, String)], Option<&str>) -> Result<Option<ProbeInfo>>;
/// resolve_page or a stand-in.
pub type PageResolver<'a> = &'a dyn Fn(&str) -> Result<Value>;

fn page_link_refusal(e: &Error) -> bool {
    e.is_rejection() && e.message().contains(PAGE_LINK)
}

/// What to relay for the chosen source, and its probe.
///
/// A stream link is probed directly. A page link (no media type or extension) goes to yt-dlp; if yt-dlp does not know
/// the page, the link is tried as a stream after all, and an HTML answer gives a clear "web page" refusal.
pub fn prepare_target(web: &Web, src: &Source, probe: UrlProbe, resolve: PageResolver, allow_local: bool) -> Result<(Target, Option<ProbeInfo>)> {
    let url = src.src.as_str();
    let site = hostname(split(url).netloc);
    let (headers, ua) = (request_headers(web), web.ua.clone());
    let title = web_title(web, src);
    let target = |url: &str, headers: Headers, ua: Option<String>, kind, via, title: String, hint: String, audio: Option<String>| Target {
        url: url.to_owned(), audio, headers, ua, kind, site: site.clone(), via, title, hint,
    };
    let direct = |page_like: bool| -> Result<(Target, Option<ProbeInfo>)> {
        let info = probe(url, &headers, ua.as_deref()).map_err(|e| match e {
            Error::ProbeFailed { kind: "invalid", .. } if page_like => Error::Rejected(format!("{PAGE_LINK}: {PAGE_HELP}")),
            e => e,
        })?;
        Ok((target(url, headers.clone(), ua.clone(), source_kind(src), "direct", title.clone(), name_from_url(url), None), info))
    };
    if looks_like_media(src) {
        return direct(false);
    }
    let data = match resolve(url) {
        Ok(data) => data,
        Err(Error::PageUnsupported(_)) => {
            return direct(true).map_err(|e| {
                if page_link_refusal(&e) {
                    Error::Rejected(format!("{PAGE_LINK}, and yt-dlp found no video on it: {PAGE_HELP}"))
                } else {
                    e
                }
            });
        }
        Err(e) if e.is_rejection() && e.message().contains(NO_YTDLP) => {
            return direct(true).map_err(|inner| if page_link_refusal(&inner) { e } else { inner }); // say yt-dlp is missing
        }
        Err(e) => return Err(e),
    };
    let (video, audio) = pick_formats(&data)?;
    for f in std::iter::once(video).chain(audio) {
        validate_url(&f["url"], allow_local)?;
    }
    let (vh, vua) = relay_headers(video);
    let kind = if video.get("protocol").map_or_else(String::new, js::text).starts_with("m3u8") { "hls" } else { "progressive" };
    let video_url = video["url"].as_str().unwrap_or_default();
    let info = probe(video_url, &vh, vua.as_deref())?;
    let page_title = py_strip(&data.get("title").filter(|t| js::truthy(t)).map_or_else(String::new, js::text)).to_owned();
    let page_title = if page_title.is_empty() { title.clone() } else { page_title };
    let audio_url = audio.and_then(|a| a["url"].as_str()).map(str::to_owned);
    Ok((target(video_url, vh, vua, kind, "yt-dlp", page_title, vr_hint(&data), audio_url), info))
}

/// A web video ready to hand to the renderer: the relay's read end and the playing stream.
pub struct WebOpened {
    pub fd: OwnedFd,
    pub title: String,
    pub format: Format,
    /// `player` (the request named it), detect_format's sources, `title` (the page's title and tags) or `chosen`.
    pub source: &'static str,
    pub warnings: Vec<String>,
    pub stream: WebStream,
}

/// Check the web request, pick the source, probe it over the network, start the relay (VideoControl._prepare_web).
pub fn open_web(
    payload: &Map<String, Value>,
    chosen: Option<Format>,
    allow_local: bool,
    probe: UrlProbe,
    resolve: PageResolver,
    factory: RelayFactory,
) -> Result<WebOpened> {
    let web = web_request(payload, allow_local)?;
    let src = choose_source(&web);
    let player = dl8_format(src.format.as_deref().or(web.format.as_deref()), web.fov)?;
    // A stream link is probed as is; a video page link is resolved with yt-dlp first.
    let (target, info) = prepare_target(&web, src, probe, resolve, allow_local)?;
    let (mut format, mut source) = match player {
        Some(f) => (f, "player"),
        None => {
            let (f, s) = video::detect_format(&target.hint, info.as_ref())?;
            (f, if s == "filename" && target.via == "yt-dlp" { "title" } else { s })
        }
    };
    if let Some(chosen) = chosen {
        (format, source) = (chosen, "chosen");
    }
    let mut warnings = video::check_limits(info.as_ref())?;
    let title = video::ascii_text(&target.title);
    let mut stream = WebStream::new(target, info, factory, None);
    if !stream.seekable() {
        warnings.push(String::from("live stream: no seeking"));
    }
    let start = if web.start >= 2.0 && stream.duration.is_none_or(|d| web.start < d - 5.0) { web.start } else { 0.0 };
    let fd = stream.start(start)?; // a failed start drops (stops) the stream
    Ok(WebOpened { fd, title, format, source, warnings, stream })
}

#[cfg(test)]
mod tests;
