//! Replays `rust/corpus/view/view.json` (the Python's answers, from `tests/native_spike/view_corpus.py`) and the
//! shared `control_frames.json` fixture against the port, byte for byte.
//!
//! A case the Python refuses `"by": "type"` must not decode into the typed arguments; one refused `"by": "value"`
//! must decode and then be refused with the same message.

use super::environment;
use super::environment::Environment;
use super::immersive::{self, PRESETS};
use super::quality::{self, FILTERS, Quality};
use super::view::{self, Menu, Mode, Settings, ViewOptions, ViewPayload, ViewState};
use crate::geometry::{Desktop, Placement};
use crate::pyjson::{Json, compact, float_repr};
use crate::testdir::TestDir;
use serde::Deserialize;
use serde::de::{DeserializeOwned, IgnoredAny};
use serde_json::Value;
use std::os::unix::fs::PermissionsExt;

const CORPUS: &str = include_str!("../../../../corpus/view/view.json");
const CONTROL_FRAMES: &str =
    include_str!("../../../../../tests/native_spike/fixtures/control_frames.json");

/// What one call gave: an answer, a refusal with the Python's message, or arguments the types cannot hold.
#[derive(Debug, PartialEq)]
enum Outcome {
    Out(Value),
    Refused(String),
    Undecodable,
}

/// A background's wire name as the `'static` one `Environment` holds.
fn background(name: &str) -> Option<&'static str> {
    environment::BACKGROUNDS
        .iter()
        .map(|(n, _)| *n)
        .find(|n| *n == name)
}

fn decode<T: DeserializeOwned>(j: &Json) -> Option<T> {
    serde_json::from_value(j.to_value()).ok()
}

fn out<T: serde::Serialize>(v: T) -> Outcome {
    Outcome::Out(serde_json::to_value(v).expect("serializable"))
}

fn refusal<T>(r: super::python::Result<T>, ok: impl FnOnce(T) -> Outcome) -> Outcome {
    match r {
        Ok(v) => ok(v),
        Err(e) => Outcome::Refused(e.0),
    }
}

/// The outcome the corpus case expects.
fn expected(case: &Json) -> Outcome {
    if let Some(out) = case.get("out") {
        return Outcome::Out(out.to_value());
    }
    let refused = case
        .get("refused")
        .and_then(Json::as_str)
        .expect("out or refused")
        .to_string();
    match case.get("by").and_then(Json::as_str) {
        Some("type") => Outcome::Undecodable,
        Some("value") => Outcome::Refused(refused),
        other => panic!("refusal without a kind: {other:?}"),
    }
}

fn corpus() -> Json {
    Json::parse(CORPUS).expect("corpus is JSON")
}

struct Envelope {
    run_id: String,
    session: String,
    request_id: u64,
}

fn envelope(c: &Json) -> Envelope {
    let e = c.get("envelope").expect("envelope");
    Envelope {
        run_id: e
            .get("run_id")
            .and_then(Json::as_str)
            .expect("run_id")
            .into(),
        session: e
            .get("session")
            .and_then(Json::as_str)
            .expect("session")
            .into(),
        request_id: e
            .get("request_id")
            .and_then(Json::as_int)
            .expect("request_id") as u64,
    }
}

fn pairs(v: &Option<Vec<(String, String)>>) -> Option<Vec<(&str, &str)>> {
    v.as_ref()
        .map(|v| v.iter().map(|(a, b)| (a.as_str(), b.as_str())).collect())
}

fn strs(v: &[String]) -> Vec<&str> {
    v.iter().map(String::as_str).collect()
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct BindArgs {
    #[serde(default)]
    binds: Option<Vec<(String, String)>>,
    #[serde(default)]
    send_binds: Option<Vec<(String, String)>>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct HelpArgs {
    visible: bool,
    #[serde(default)]
    turn: i64,
    #[serde(default)]
    binds: Option<Vec<(String, String)>>,
    #[serde(default)]
    send_binds: Option<Vec<(String, String)>>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct DesktopsArgs {
    names: Vec<String>,
    #[serde(default)]
    renew: Vec<String>,
    #[serde(default)]
    ease: bool,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct PlacementArgs {
    placements: Vec<Placement>,
    #[serde(default)]
    glide: Option<i64>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct EasedArgs {
    p: Placement,
    to: Placement,
    e: f64,
}

#[derive(Deserialize)]
struct One<T> {
    #[serde(
        alias = "rgb",
        alias = "menu",
        alias = "state",
        alias = "name",
        alias = "chord",
        alias = "t",
        alias = "scale"
    )]
    v: T,
}

fn frame_case(env: &Envelope, f: &str, args: &Json) -> Outcome {
    let (run, session, rid) = (env.run_id.as_str(), env.session.as_str(), env.request_id);
    macro_rules! args {
        ($t:ty) => {
            match decode::<$t>(args) {
                Some(a) => a,
                None => return Outcome::Undecodable,
            }
        };
    }
    match f {
        "help_frame" => {
            let a = args!(HelpArgs);
            let r = view::help_frame(
                run,
                session,
                rid,
                a.visible,
                a.turn,
                pairs(&a.binds).as_deref(),
                pairs(&a.send_binds).as_deref(),
            );
            refusal(r, out)
        }
        "style_frame" => out(view::style_frame(run, session, rid, args!(One<[u8; 3]>).v)),
        "desktops_frame" => {
            let a = args!(DesktopsArgs);
            refusal(
                view::desktops_frame(run, session, rid, &strs(&a.names), &strs(&a.renew), a.ease),
                out,
            )
        }
        "placement_frame" => {
            let a = args!(PlacementArgs);
            refusal(
                view::placement_frame(run, session, rid, &a.placements, a.glide),
                out,
            )
        }
        "placement_menu_frame" => refusal(
            view::placement_menu_frame(run, session, rid, &args!(One<Menu>).v),
            out,
        ),
        "view_frame" => out(view::view_frame(
            run,
            session,
            rid,
            &args!(One<ViewPayload>).v,
        )),
        "focus_command" => refusal(view::focus_command(&args!(One<String>).v), out),
        "parse_border" => out(view::parse_border(args.get("reply").expect("reply"))),
        "chord_label" => out(view::chord_label(&args!(One<String>).v)),
        "help_rows" | "help_sections" => {
            let a = args!(BindArgs);
            let (binds, send) = (pairs(&a.binds), pairs(&a.send_binds));
            if f == "help_rows" {
                refusal(view::help_rows(binds.as_deref(), send.as_deref()), out)
            } else {
                refusal(view::help_sections(binds.as_deref(), send.as_deref()), out)
            }
        }
        "smootherstep" => out(float_repr(view::smootherstep(args!(One<f64>).v))),
        "eased_to" => {
            let a = args!(EasedArgs);
            out(view::eased_to(&a.p, &a.to, a.e))
        }
        "scale_percent" => out(view::scale_percent(args!(One<f64>).v)),
        other => panic!("unknown corpus fn {other}"),
    }
}

#[test]
fn frames_match_the_python_byte_for_byte() {
    let c = corpus();
    let env = envelope(&c);
    let frames = c.get("frames").and_then(|f| match f {
        Json::Array(a) => Some(a),
        _ => None,
    });
    let frames = frames.expect("frames");
    assert!(frames.len() > 100, "corpus has {} frames", frames.len());
    let mut failed = Vec::new();
    for case in frames {
        let f = case.get("fn").and_then(Json::as_str).expect("fn");
        let args = case.get("args").expect("args");
        let got = frame_case(&env, f, args);
        let want = expected(case);
        if got != want {
            failed.push(format!(
                "{f} {:?}\n  got  {got:?}\n  want {want:?}",
                args.to_value()
            ));
        }
    }
    assert!(
        failed.is_empty(),
        "{} of {} frames differ:\n{}",
        failed.len(),
        frames.len(),
        failed.join("\n")
    );
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Init {
    names: Vec<String>,
    spacing: Option<i64>,
    prediction: Option<bool>,
    placements: Option<Vec<Placement>>,
    stabilization: Option<i64>,
    // Named so `deny_unknown_fields` accepts them, then taken from the order-keeping `Json` instead.
    #[allow(dead_code)]
    video_quality: Option<IgnoredAny>,
    #[allow(dead_code)]
    environment: Option<IgnoredAny>,
    #[allow(dead_code)]
    immersive: Option<IgnoredAny>,
}

fn new_state(init: &Json) -> Outcome2 {
    let Some(i) = decode::<Init>(init) else {
        return Outcome2::Undecodable;
    };
    let d = ViewOptions::default();
    let options = ViewOptions {
        spacing: i.spacing.unwrap_or(d.spacing),
        prediction: i.prediction.unwrap_or(d.prediction),
        placements: i.placements.unwrap_or_default(),
        stabilization: i.stabilization.unwrap_or(d.stabilization),
        video_quality: quality::clean(
            &init
                .get("video_quality")
                .map_or(Value::Null, Json::to_value),
        ),
        environment: environment::clean(
            &init.get("environment").map_or(Value::Null, Json::to_value),
        ),
        immersive: immersive::clean(&init.get("immersive").map_or(Value::Null, Json::to_value)),
    };
    match ViewState::new(&strs(&i.names), options) {
        Ok(s) => Outcome2::State(Box::new(s)),
        Err(e) => Outcome2::Refused(e.0),
    }
}

enum Outcome2 {
    State(Box<ViewState>),
    Refused(String),
    Undecodable,
}

/// The `Environment` a script assigns straight onto the state (the Python sets the attribute, uncleaned).
fn raw_environment(j: &Json) -> Option<Environment> {
    let name = |k| j.get(k).and_then(Json::as_str).and_then(background);
    Some(Environment {
        background: name("background")?,
        last: name("last")?,
        brightness: j.get("brightness").and_then(Json::as_int)?,
    })
}

fn step(s: &mut ViewState, call: &str, args: &Json, kwargs: Option<&Json>) -> Outcome {
    macro_rules! args {
        ($t:ty) => {
            match decode::<$t>(args) {
                Some(a) => a,
                None => return Outcome::Undecodable,
            }
        };
    }
    let none = |_| Outcome::Out(Value::Null);
    match call {
        "apply" => refusal(s.apply(&args!((String,)).0), out),
        "apply_menu" => refusal(s.apply_menu(&args!((String,)).0), out),
        "slide" => {
            let (field, t) = args!((String, f64));
            refusal(s.slide(&field, t), out)
        }
        "open_menu" => refusal(s.open_menu(args!((i64,)).0), none),
        "hold_surface" => refusal(s.hold_surface(args!((Option<Placement>,)).0.as_ref()), none),
        "set_placements" => refusal(s.set_placements(&args!((Vec<Placement>,)).0), out),
        "hold_placements" => refusal(s.hold_placements(&args!((Vec<Placement>,)).0), none),
        "set_desktops" => {
            #[derive(Deserialize, Default)]
            #[serde(deny_unknown_fields)]
            struct Kw {
                focus: Option<i64>,
                sizes: Option<Vec<Desktop>>,
            }
            let (names, placements, wide) = args!((Vec<String>, Vec<Placement>, Vec<bool>));
            let kw = match kwargs {
                Some(k) => match decode::<Kw>(k) {
                    Some(k) => k,
                    None => return Outcome::Undecodable,
                },
                None => Kw::default(),
            };
            refusal(
                s.set_desktops(
                    &strs(&names),
                    &placements,
                    &wide,
                    kw.focus,
                    kw.sizes.as_deref(),
                ),
                none,
            )
        }
        "set" => {
            let Json::Array(a) = args else {
                panic!("set args")
            };
            let attr = a[0].as_str().expect("attr");
            if attr == "environment" {
                s.environment = raw_environment(&a[1]).expect("a valid environment");
                return Outcome::Out(Value::Null);
            }
            let v = a[1].as_bool().expect("a bool attribute");
            match attr {
                "typing" => s.typing = v,
                "remove_armed" => s.remove_armed = v,
                "scale_picker" => s.scale_picker = v,
                "resolution_picker" => s.resolution_picker = v,
                "desktops_busy" => s.desktops_busy = v,
                "background_picker" => s.background_picker = v,
                other => panic!("unknown attribute {other}"),
            }
            Outcome::Out(Value::Null)
        }
        other => panic!("unknown call {other}"),
    }
}

/// The view, menu, settings and wanted placements, as the corpus records them after each step.
fn snapshot(env: &Envelope, s: &ViewState) -> Value {
    let (run, session, rid) = (env.run_id.as_str(), env.session.as_str(), env.request_id);
    let or_refusal = |r: super::python::Result<String>| match r {
        Ok(v) => Value::String(v),
        Err(e) => serde_json::json!({"refused": e.0}),
    };
    serde_json::json!({
        "view": view::view_frame(run, session, rid, &s.view()),
        "menu": or_refusal(view::placement_menu_frame(run, session, rid, &s.menu())),
        "settings": or_refusal(s.settings().map(|v| compact(&v))),
        "wanted": serde_json::to_value(s.wanted_placements()).expect("placements"),
    })
}

fn want_snapshot(j: &Json) -> Value {
    let keys = ["view", "menu", "settings", "wanted"];
    Value::Object(
        keys.iter()
            .map(|k| (k.to_string(), j.get(k).expect("snapshot key").to_value()))
            .collect(),
    )
}

#[test]
fn scripts_match_the_python_step_by_step() {
    let c = corpus();
    let env = envelope(&c);
    let Some(Json::Array(scripts)) = c.get("scripts") else {
        panic!("scripts")
    };
    let mut failed = Vec::new();
    let mut steps = 0;
    for script in scripts {
        let name = script.get("name").and_then(Json::as_str).expect("name");
        let init = script.get("init").expect("init");
        let mut state = match (new_state(init), script.get("refused")) {
            (Outcome2::State(s), None) => s,
            (Outcome2::Refused(got), Some(want))
                if script.get("by").and_then(Json::as_str) == Some("value") =>
            {
                if Some(got.as_str()) != want.as_str() {
                    failed.push(format!("{name}: init refused {got:?}, want {want:?}"));
                }
                continue;
            }
            (Outcome2::Undecodable, Some(_))
                if script.get("by").and_then(Json::as_str) == Some("type") =>
            {
                continue;
            }
            (Outcome2::State(_), Some(want)) => {
                failed.push(format!("{name}: init accepted, want refused {want:?}"));
                continue;
            }
            (Outcome2::Refused(got), _) => {
                failed.push(format!("{name}: init refused {got:?}"));
                continue;
            }
            (Outcome2::Undecodable, _) => {
                failed.push(format!("{name}: init does not decode"));
                continue;
            }
        };
        if snapshot(&env, &state) != want_snapshot(script.get("start").expect("start")) {
            failed.push(format!(
                "{name}: start\n  got  {}\n  want {}",
                snapshot(&env, &state),
                want_snapshot(script.get("start").unwrap())
            ));
            continue;
        }
        let Some(Json::Array(script_steps)) = script.get("steps") else {
            panic!("{name}: steps")
        };
        for (n, st) in script_steps.iter().enumerate() {
            steps += 1;
            let call = st.get("call").and_then(Json::as_str).expect("call");
            let got = step(
                &mut state,
                call,
                st.get("args").expect("args"),
                st.get("kwargs"),
            );
            let want = expected(st);
            if got != want {
                failed.push(format!(
                    "{name} step {n} {call}: got {got:?}, want {want:?}"
                ));
                break;
            }
            let (got, want) = (snapshot(&env, &state), want_snapshot(st));
            if got != want {
                failed.push(format!(
                    "{name} step {n} {call}: state\n  got  {got}\n  want {want}"
                ));
                break;
            }
        }
    }
    assert!(steps > 50, "corpus has {steps} steps");
    assert!(
        failed.is_empty(),
        "{} scripts differ:\n{}",
        failed.len(),
        failed.join("\n")
    );
}

#[test]
fn load_settings_matches_the_python() {
    let c = corpus();
    let Some(Json::Array(cases)) = c.get("load_settings") else {
        panic!("load_settings")
    };
    let dir = TestDir::new("view-load");
    let mut failed = Vec::new();
    for (n, case) in cases.iter().enumerate() {
        let path = dir.path().join(format!("layout-{n}.json"));
        if let Some(text) = case.get("text").and_then(Json::as_str) {
            std::fs::write(&path, text).expect("write");
        }
        let got = compact(&view::load_settings(&path));
        let want = case
            .get("settings")
            .and_then(Json::as_str)
            .expect("settings");
        if got != want {
            failed.push(format!(
                "{:?}\n  got  {got}\n  want {want}",
                case.get("text")
            ));
        }
    }
    assert!(
        failed.is_empty(),
        "{} of {} differ:\n{}",
        failed.len(),
        cases.len(),
        failed.join("\n")
    );
}

fn exact_keys(j: &Json, keys: &[&str]) -> bool {
    j.as_object()
        .is_some_and(|o| o.len() == keys.len() && o.iter().all(|(k, _)| keys.contains(&k.as_str())))
}

/// A raw settings dict as the typed `Settings`, or None for one the types cannot hold (the Python's type refusals:
/// a missing or extra key, a bool for an int, a name that is not a choice, the old filter alias).
fn typed_settings(j: &Json) -> Option<Settings> {
    let top = [
        "spacing",
        "mode",
        "prediction",
        "stabilization",
        "video_quality",
        "environment",
        "immersive",
    ];
    if !exact_keys(j, &top) {
        return None;
    }
    let vq = j.get("video_quality")?;
    let env = j.get("environment")?;
    let im = j.get("immersive")?;
    let base = ["global", "desktops", "preset", "strength", "convergence"];
    let with_strengths = [
        "strengths",
        "global",
        "desktops",
        "preset",
        "strength",
        "convergence",
    ];
    if !exact_keys(vq, &["filter", "deband"])
        || !exact_keys(env, &["background", "last", "brightness"])
        || !(exact_keys(im, &base) || exact_keys(im, &with_strengths))
    {
        return None;
    }
    let filter = vq.get("filter")?.as_str()?;
    let entries = |v: &Json| v.as_object().map(<[_]>::to_vec);
    let strengths = match im.get("strengths") {
        None => None,
        Some(s) => Some(
            entries(s)?
                .into_iter()
                .map(|(k, v)| Some((k, v.as_int()?)))
                .collect::<Option<std::collections::BTreeMap<_, _>>>()?,
        ),
    };
    let desktops = entries(im.get("desktops")?)?
        .into_iter()
        .map(|(k, v)| Some((k, v.as_bool()?)))
        .collect::<Option<_>>()?;
    let background = |k| env.get(k)?.as_str().and_then(background);
    Some(Settings {
        spacing: j.get("spacing")?.as_int()?,
        mode: Mode::parse(j.get("mode")?.as_str()?)?,
        prediction: j.get("prediction")?.as_bool()?,
        stabilization: j.get("stabilization")?.as_int()?,
        video_quality: Quality {
            filter: FILTERS.iter().position(|(f, _)| *f == filter)?,
            deband: vq.get("deband")?.as_bool()?,
        },
        environment: Environment {
            background: background("background")?,
            last: background("last")?,
            brightness: env.get("brightness")?.as_int()?,
        },
        immersive: immersive::Setting {
            strengths,
            global: im.get("global")?.as_bool()?,
            desktops,
            preset: PRESETS
                .iter()
                .find(|p| Some(p.name) == im.get("preset").and_then(Json::as_str))?,
            strength: im.get("strength")?.as_int()?,
            convergence: im.get("convergence")?.as_int()?,
        },
    })
}

#[test]
fn save_settings_matches_the_python() {
    let c = corpus();
    let Some(Json::Array(cases)) = c.get("save_settings") else {
        panic!("save_settings")
    };
    let dir = TestDir::new("view-save");
    let mut failed = Vec::new();
    for (n, case) in cases.iter().enumerate() {
        let raw = case.get("settings").expect("settings");
        let parent = dir.path().join(format!("cfg-{n}")).join("viture-workspace");
        let path = parent.join("layout.json");
        let got = match typed_settings(raw) {
            None => Outcome::Undecodable,
            Some(s) => match view::save_settings(&path, &s) {
                Ok(()) => {
                    let mode = |p: &std::path::Path| {
                        std::fs::metadata(p).expect("stat").permissions().mode() & 0o777
                    };
                    assert_eq!((mode(&path), mode(&parent)), (0o600, 0o700), "case {n}");
                    Outcome::Out(Value::String(std::fs::read_to_string(&path).expect("read")))
                }
                Err(e) => {
                    assert_eq!(e.kind(), std::io::ErrorKind::InvalidInput, "case {n}");
                    Outcome::Refused(e.to_string())
                }
            },
        };
        let want = match case.get("bytes") {
            Some(b) => Outcome::Out(b.to_value()),
            None => expected(case),
        };
        if got != want {
            failed.push(format!(
                "case {n} {}\n  got  {got:?}\n  want {want:?}",
                raw.to_value()
            ));
        }
    }
    assert!(
        failed.is_empty(),
        "{} of {} differ:\n{}",
        failed.len(),
        cases.len(),
        failed.join("\n")
    );
}

/// Every view, style, help, placement, placement_menu and desktops frame in the shared wire fixture re-encodes to
/// the same bytes through the port's encoders.
#[test]
fn control_frame_fixture_reencodes_byte_for_byte() {
    let fixture = Json::parse(CONTROL_FRAMES).expect("fixture is JSON");
    let ops = fixture.get("ops").expect("ops");
    let mut checked = 0;
    for op in [
        "view",
        "style",
        "help",
        "placement",
        "placement_menu",
        "desktops",
    ] {
        let Some(Json::Array(encoded)) = ops.get(op).and_then(|o| o.get("encoded")) else {
            panic!("{op} frames")
        };
        assert!(!encoded.is_empty(), "{op}");
        for line in encoded {
            let line = line.as_str().expect("an encoded frame");
            let f = Json::parse(line).expect("frame JSON");
            let s = |k| {
                f.get(k)
                    .and_then(Json::as_str)
                    .expect("envelope string")
                    .to_string()
            };
            let (run, session) = (s("run_id"), s("session"));
            let rid = f
                .get("request_id")
                .and_then(Json::as_int)
                .expect("request_id") as u64;
            let p = f.get("payload").expect("payload");
            let names = |k| -> Vec<String> {
                p.get(k)
                    .map(|v| decode(v).expect("names"))
                    .unwrap_or_default()
            };
            let got = match op {
                "view" => view::view_frame(&run, &session, rid, &decode(p).expect("view payload")),
                "style" => view::style_frame(
                    &run,
                    &session,
                    rid,
                    decode(p.get("border").expect("border")).expect("rgb"),
                ),
                "help" => {
                    let visible = p.get("visible").and_then(Json::as_bool).expect("visible");
                    let turn = p.get("turn").and_then(Json::as_int).unwrap_or(0);
                    view::help_frame(&run, &session, rid, visible, turn, None, None).expect("help")
                }
                "placement" => {
                    let desktops: Vec<Placement> =
                        decode(p.get("desktops").expect("desktops")).expect("placements");
                    let glide = p.get("glide").and_then(Json::as_int);
                    view::placement_frame(&run, &session, rid, &desktops, glide).expect("placement")
                }
                "placement_menu" => {
                    view::placement_menu_frame(&run, &session, rid, &decode(p).expect("menu"))
                        .expect("menu")
                }
                "desktops" => {
                    let (outputs, renew) = (names("outputs"), names("renew"));
                    let ease = p.get("ease").and_then(Json::as_bool).unwrap_or(false);
                    view::desktops_frame(&run, &session, rid, &strs(&outputs), &strs(&renew), ease)
                        .expect("desktops")
                }
                _ => unreachable!(),
            };
            assert_eq!(
                got.trim_end_matches('\n'),
                line.trim_end_matches('\n'),
                "{op}"
            );
            assert!(got.ends_with('\n'));
            checked += 1;
        }
    }
    assert!(checked >= 30, "{checked} frames");
}
