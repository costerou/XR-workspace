//! ownership.py: the run's client of the Hyprland ownership plugin (the broker), schema 1.
//!
//! A port of `OwnershipBrokerClient`. The requests and the checks of each reply are [`xr_wire::plugin`]'s; this adds
//! what the Python adds round them: a session proof before and after every lifecycle request, the hello's fallback, the
//! write-ahead journal round each create, the readback that must match what was asked, and the removal that is only
//! believed after a separate readback. Messages are the Python's, word for word, because they end up in a run's
//! reasons. The isolated-VM schema-2 client (`IsolatedBrokerClient`) is not ported: the Rust supervisor runs panel
//! sessions only.

use std::time::Duration;

use xr_wire::json::{self, Style, Value};
use xr_wire::plugin::{self, Op, OutputSpec, Request, Schema};

use crate::hyprland::{Client, DEADLINE};
use crate::journal::{Journal, Observed};
use crate::recovery::{BackendError, OutputView, RecoveryBackend, WorkspaceView};

/// A refusal, in the Python's words.
pub type Result<T> = std::result::Result<T, String>;

/// ownership.CURSOR_TIMEOUT: a cursor-shape read waits no longer than the sampler's own cursor reads.
pub const CURSOR_TIMEOUT: Duration = Duration::from_millis(250);

/// ownership.CURSOR_NAME: `[A-Za-z0-9][A-Za-z0-9_.-]{0,63}`, the whole name.
fn cursor_name(s: &str) -> bool {
    let b = s.as_bytes();
    (1..=64).contains(&b.len())
        && b[0].is_ascii_alphanumeric()
        && b[1..].iter().all(|c| c.is_ascii_alphanumeric() || b"_.-".contains(c))
}

fn blocked<T>(why: &str) -> Result<T> {
    Err(why.to_owned())
}

/// The step of its removal the broker stopped at (the reply's `blocked`), in plain words.
const REMOVAL_STEPS: [(&str, &str); 5] = [
    (
        "unavailable",
        "the ownership plugin no longer holds a live output for it, or the fallback monitor is not usable",
    ),
    (
        "evacuate",
        "its windows and workspaces could not be moved off it (a pinned window, a special workspace, or a monitor gone)",
    ),
    (
        "unreachable",
        "after the move, a window was still not reachable on a usable monitor",
    ),
    ("rule", "its monitor rule could not be cleaned up"),
    (
        "destroy",
        "the output did not go away when it was destroyed",
    ),
];

const STATES: [&str; 6] = ["pending", "failed", "live", "invalid", "dead", "blocked"];

fn removal_step(key: &str) -> Option<&'static str> {
    REMOVAL_STEPS
        .iter()
        .find(|(k, _)| *k == key)
        .map(|(_, v)| *v)
}

/// `_brief`: bounded, single-line ASCII rendering of compositor-supplied readback for diagnostics.
fn brief(value: Option<&Value>) -> String {
    let value = match value {
        None => Value::Null,
        Some(Value::Array(items)) if items.len() > 8 => {
            let mut head = items[..8].to_vec();
            head.push(Value::String(format!("... {} more", items.len() - 8)));
            Value::Array(head)
        }
        Some(v) => v.clone(),
    };
    let text = json::encode(&value, Style::SORTED);
    if text.len() <= 300 {
        text
    } else {
        format!("{}...", &text[..297])
    }
}

fn token(value: &str) -> Result<String> {
    if plugin::is_token(value) {
        Ok(value.to_owned())
    } else {
        blocked("invalid identifier")
    }
}

fn is_uuid(s: &str) -> bool {
    s.len() == 36
        && s.bytes().enumerate().all(|(i, c)| match i {
            8 | 13 | 18 | 23 => c == b'-',
            _ => matches!(c, b'0'..=b'9' | b'a'..=b'f'),
        })
}

fn number(v: Option<&Value>) -> Option<f64> {
    match v {
        Some(Value::Number(n)) => n.as_f64().filter(|x| x.is_finite()),
        _ => None,
    }
}

/// The run's broker client. `epoch` and `fallback` come from the hello (or the journal, in recovery).
#[derive(Clone, Debug)]
pub struct Broker {
    pub run_id: String,
    pub session: String,
    /// The physical monitor the consent names as the fallback.
    pub approved_physical: String,
    pub epoch: Option<String>,
    /// The hello's fallback handle.
    pub fallback: Option<String>,
    pub request_id: u32,
    /// Why the last `evacuate_remove` did not remove its output, in plain words.
    pub refusal: Option<String>,
}

impl Broker {
    pub fn new(run_id: &str, session: &str, approved_physical: &str) -> Result<Broker> {
        Ok(Broker {
            run_id: token(run_id)?,
            session: token(session)?,
            approved_physical: token(approved_physical)?,
            epoch: None,
            fallback: None,
            request_id: 0,
            refusal: None,
        })
    }

    /// `from_journal`: the authority the journal saved, never a new epoch or fallback.
    pub fn from_journal(client: &mut Client, journal: &mut Journal) -> Result<Broker> {
        journal.begin().map_err(|e| e.to_string())?;
        let Some(context) = journal.broker.clone() else {
            return blocked("invalid broker context");
        };
        if context.get("mode").is_some() {
            return blocked("isolated journal requires isolated recovery");
        }
        let field = |k: &str| context.get(k).and_then(Value::as_str).map(str::to_owned);
        if field("instance").as_deref() != Some(client.instance()) {
            return blocked("saved compositor instance mismatch");
        }
        let fallback_name = field("recovery_output").ok_or("invalid broker context")?;
        let mut broker = Broker::new(&journal.run_id, &journal.session, &fallback_name)?;
        broker.epoch = field("epoch");
        broker.fallback = field("fallback");
        broker.verified_session(client)?;
        Ok(broker)
    }

    pub fn verified_session(&self, client: &mut Client) -> Result<String> {
        let observed = client.verified_session().map_err(|e| e.to_string())?;
        if observed != self.session {
            return blocked("compositor session changed");
        }
        Ok(observed)
    }

    /// `_call`: one request, its reply checked to the letter. `glance`: a read that only changes how something looks,
    /// with no session proof round it.
    fn call(&mut self, client: &mut Client, op: Op, glance: Option<Duration>) -> Result<Value> {
        if glance.is_none() {
            self.verified_session(client)?;
        }
        self.request_id += 1;
        if self.request_id > plugin::MAX_REQUEST_ID {
            return blocked("request capacity");
        }
        let hello = op.name() == Schema::Legacy.hello();
        let request = Request {
            schema: Schema::Legacy,
            session: self.session.clone(),
            run_id: self.run_id.clone(),
            request_id: self.request_id,
            epoch: self.epoch.clone(),
            op,
        };
        let wire = request.encode().map_err(|e| {
            if e.0.contains("exceeds") {
                "broker request exceeds 8 KiB".to_owned()
            } else {
                e.0
            }
        })?;
        let raw = client
            .broker_request(&wire, glance.unwrap_or(DEADLINE))
            .map_err(|e| e.to_string())?;
        let reply = plugin::decode_reply(&raw, &request).map_err(|e| e.0)?;
        if glance.is_none() {
            self.verified_session(client)?;
        }
        if hello {
            self.epoch = Some(reply.epoch);
        }
        Ok(reply.result)
    }

    /// The hello, once: the broker qualifies the run and names its fallback.
    pub fn require_output_ownership(&mut self, client: &mut Client) -> Result<()> {
        if self.epoch.is_some() {
            return Ok(());
        }
        let hello = (|| {
            let result = self.call(
                client,
                Op::Hello {
                    fallback_name: self.approved_physical.clone(),
                },
                None,
            )?;
            let keys = result.keys().unwrap_or_default();
            if keys.len() != 2
                || !keys.contains("qualified")
                || !keys.contains("fallback")
                || result.get("qualified") != Some(&Value::Bool(true))
            {
                return blocked("broker handshake not qualified");
            }
            self.fallback = Some(token(
                result.get("fallback").and_then(Value::as_str).unwrap_or(""),
            )?);
            Ok(())
        })();
        if hello.is_err() {
            self.epoch = None;
            self.fallback = None;
        }
        hello
    }

    /// `cursor_shape()`: the name of the cursor the app under the pointer asked for while the pointer is on one of this
    /// run's live XR outputs, `None` if there is none (elsewhere, or a cursor given as a picture). A read-only glance
    /// (`CURSOR_TIMEOUT`, no session proof round it); fails as any broker call.
    pub fn cursor_shape(&mut self, client: &mut Client) -> Result<Option<String>> {
        let result = self.call(client, Op::CursorShape, Some(CURSOR_TIMEOUT))?;
        let shape = match (result.keys(), result.get("shape")) {
            (Some(keys), Some(Value::String(shape))) if keys.len() == 1 => shape.clone(),
            _ => return blocked("invalid cursor shape"),
        };
        if !shape.is_empty() && !cursor_name(&shape) {
            return blocked("invalid cursor shape");
        }
        Ok(Some(shape).filter(|s| !s.is_empty()))
    }

    pub fn inspect(&mut self, client: &mut Client, id: &str) -> Result<Value> {
        if !is_uuid(id) {
            return blocked("invalid UUID");
        }
        self.require_output_ownership(client)?;
        let result = self.call(client, Op::Inspect { id: id.to_owned() }, None)?;
        identity(&result, id)?;
        Ok(result)
    }

    /// `create_journaled`: each output's intent in the journal, then the create, then a readback that must match.
    pub fn create_journaled(
        &mut self,
        client: &mut Client,
        specs: &[OutputSpec],
        journal: &mut Journal,
    ) -> Result<Vec<String>> {
        if journal.run_id != self.run_id || journal.session != self.session {
            return blocked("journal identity mismatch");
        }
        self.require_output_ownership(client)?;
        journal.begin().map_err(|e| e.to_string())?;
        if !journal.records.is_empty() {
            return blocked("existing run requires recovery, not creation");
        }
        let mut names = Vec::new();
        for spec in specs {
            let intent = Value::Object(vec![
                ("kind".into(), Value::String("output".into())),
                ("id".into(), Value::String(spec.id.clone())),
                ("name".into(), Value::String(spec.name.clone())),
                ("index".into(), Value::Number(spec.index.into())),
                ("before".into(), Value::Null),
            ]);
            let record = journal.prepare(&intent).map_err(|e| e.to_string())?;
            self.call(client, Op::Create(spec.clone()), None)?; // a receipt is not readback
            let observed = self.inspect(client, &spec.id)?;
            verify(&observed, spec)?;
            let seen = Observed::Output {
                id: spec.id.clone(),
                name: spec.name.clone(),
                physical: false,
            };
            journal.confirm(&record, &seen).map_err(|e| e.to_string())?;
            names.push(spec.name.clone());
        }
        Ok(names)
    }

    fn refused(&self, reply: &Value) -> String {
        let blocked = reply
            .get("blocked")
            .and_then(Value::as_str)
            .and_then(removal_step);
        let state = reply.get("state").and_then(Value::as_str).unwrap_or("");
        let mut said = vec![blocked.map(str::to_owned).unwrap_or_else(|| {
            format!("the ownership plugin refused to remove it (state {state})")
        })];
        if reply.get("fallback_usable") != Some(&Value::Bool(true)) {
            said.push(format!(
                "{} cannot take its windows: it is disabled, mirrored or unplugged",
                self.approved_physical
            ));
        }
        said.join("; ")
    }

    fn removed(reply: &Value, name: &str) -> bool {
        reply.get("name").and_then(Value::as_str) == Some(name)
            && reply.get("state").and_then(Value::as_str) == Some("dead")
            && reply.get("observation") == Some(&Value::Null)
            && reply.get("fallback_usable") == Some(&Value::Bool(true))
    }

    /// The output's windows to the fallback, then the output gone, proven by a separate readback. False (with
    /// [`Broker::refusal`]) when the broker refused.
    pub fn evacuate_remove(&mut self, client: &mut Client, id: &str, name: &str) -> Result<bool> {
        self.refusal = None;
        let observed = self.inspect(client, id)?;
        if observed.get("name").and_then(Value::as_str) != Some(name) {
            return blocked("output name/UUID mismatch");
        }
        let fallback = self.fallback.clone().unwrap_or_default();
        let op = Op::EvacuateRemove {
            id: id.to_owned(),
            name: name.to_owned(),
            fallback,
        };
        let reply = self.call(client, op, None)?;
        identity(&reply, id)?;
        if !Self::removed(&reply, name) {
            self.refusal = Some(self.refused(&reply));
            return Ok(false);
        }
        let last = self.inspect(client, id)?;
        if Self::removed(&last, name) {
            return Ok(true);
        }
        self.refusal = Some(self.refused(&last));
        Ok(false)
    }
}

/// `_identity`: the reply is about this run's output `id`, in a known lifecycle state.
fn identity(result: &Value, id: &str) -> Result<()> {
    const KEYS: [&str; 6] = [
        "id",
        "name",
        "state",
        "physical",
        "observation",
        "fallback_usable",
    ];
    let mut keys = result.keys().unwrap_or_default();
    keys.remove("blocked");
    let step = match result.get("blocked") {
        None => Some("unavailable"),
        Some(v) => v.as_str(),
    };
    if keys.len() != KEYS.len()
        || !KEYS.iter().all(|k| keys.contains(k))
        || result.get("id").and_then(Value::as_str) != Some(id)
        || result.get("physical") != Some(&Value::Bool(false))
        || step.and_then(removal_step).is_none()
    {
        return blocked("unproven broker identity");
    }
    match result.get("name").and_then(Value::as_str) {
        Some(name) => token(name)?,
        None => return blocked("invalid identifier"),
    };
    let state_ok = result
        .get("state")
        .and_then(Value::as_str)
        .is_some_and(|s| STATES.contains(&s));
    if !state_ok || !matches!(result.get("fallback_usable"), Some(Value::Bool(_))) {
        return blocked("invalid lifecycle");
    }
    Ok(())
}

/// `verify`: the output is live, usable and exactly as asked, on regular workspaces only.
pub fn verify(result: &Value, spec: &OutputSpec) -> Result<()> {
    if result.get("state").and_then(Value::as_str) != Some("live")
        || result.get("name").and_then(Value::as_str) != Some(&spec.name)
        || result.get("fallback_usable") != Some(&Value::Bool(true))
    {
        return blocked("creation incomplete");
    }
    let o = match result.get("observation") {
        Some(o @ Value::Object(_)) => o,
        _ => return blocked("missing readback"),
    };
    let wanted = [
        ("width", spec.width as f64),
        ("height", spec.height as f64),
        ("refresh_millihz", spec.refresh_millihz as f64),
        ("scale", spec.scale),
        ("x", spec.x as f64),
        ("y", spec.y as f64),
    ];
    if wanted
        .iter()
        .any(|(k, want)| number(o.get(k)) != Some(*want))
    {
        return blocked("actual geometry/workspace mismatch");
    }
    if o.get("enabled") != Some(&Value::Bool(true))
        || o.get("dpms") != Some(&Value::Bool(true))
        || o.get("mirrored") != Some(&Value::Bool(false))
        || o.get("transform").and_then(Value::as_int) != Some(0)
    {
        return blocked("unusable output");
    }
    let workspaces = o.get("workspaces");
    let mut ids: Vec<i128> = Vec::new();
    let regular = |w: &Value| {
        let keys = w.keys().unwrap_or_default();
        keys.len() == 3
            && ["id", "name", "special"].iter().all(|k| keys.contains(k))
            && w.get("id")
                .and_then(Value::as_int)
                .is_some_and(|i| 0 < i && i <= 2147483647)
            && w.get("special") == Some(&Value::Bool(false))
            && matches!(w.get("name"), Some(Value::String(_)))
    };
    let ok = match workspaces {
        Some(Value::Array(list)) if !list.is_empty() && list.iter().all(regular) => {
            ids = list
                .iter()
                .filter_map(|w| w.get("id").and_then(Value::as_int))
                .collect();
            let mut unique = ids.clone();
            unique.sort_unstable();
            unique.dedup();
            unique.len() == ids.len()
        }
        _ => false,
    };
    if !ok {
        return Err(format!(
            "workspace placement mismatch: expected regular workspaces observed {}",
            brief(workspaces)
        ));
    }
    ids.sort_unstable();
    let shown: Vec<String> = ids.iter().take(8).map(i128::to_string).collect();
    let listed = format!("[{}]", shown.join(", "));
    if !o
        .get("workspace")
        .and_then(Value::as_int)
        .is_some_and(|w| ids.contains(&w))
    {
        return Err(format!(
            "workspace placement mismatch: active {} not among {listed}",
            brief(o.get("workspace"))
        ));
    }
    let window_ok = |w: &Value| {
        let keys = w.keys().unwrap_or_default();
        keys.len() == 2
            && keys.contains("workspace")
            && keys.contains("pinned")
            && w.get("workspace")
                .and_then(Value::as_int)
                .is_some_and(|i| ids.contains(&i))
            && matches!(w.get("pinned"), Some(Value::Bool(_)))
    };
    let windows_ok =
        matches!(o.get("windows"), Some(Value::Array(list)) if list.iter().all(window_ok));
    if !windows_ok {
        let all: Vec<String> = ids.iter().map(i128::to_string).collect();
        return Err(format!(
            "unsupported or unreachable mapped window: workspaces [{}] observed {}",
            all.join(", "),
            brief(o.get("windows"))
        ));
    }
    Ok(())
}

/// The recovery view of a broker run (`recover(journal, broker)`): the session, and removals through the broker. The
/// broker never hands out workspaces or settings, nor takes raw mutations.
pub struct BrokerRecovery<'a> {
    pub client: &'a mut Client,
    pub broker: &'a mut Broker,
}

const NO_HANDLES: &str =
    "broker recovery uses handle transaction; settings/workspace mutations disabled";

impl RecoveryBackend for BrokerRecovery<'_> {
    fn session(&mut self) -> std::result::Result<String, BackendError> {
        self.broker
            .verified_session(self.client)
            .map_err(BackendError)
    }
    fn outputs(&mut self) -> std::result::Result<Vec<OutputView>, BackendError> {
        Err(BackendError(NO_HANDLES.into()))
    }
    fn workspaces(&mut self) -> std::result::Result<Vec<WorkspaceView>, BackendError> {
        Err(BackendError(NO_HANDLES.into()))
    }
    fn settings(
        &mut self,
    ) -> std::result::Result<std::collections::BTreeMap<String, i64>, BackendError> {
        Err(BackendError(NO_HANDLES.into()))
    }
    fn mutate(&mut self, _command: &str) -> std::result::Result<String, BackendError> {
        Err(BackendError("no raw mutations through broker".into()))
    }
    fn evacuate_remove(&mut self, id: &str, name: &str) -> std::result::Result<bool, BackendError> {
        self.broker
            .evacuate_remove(self.client, id, name)
            .map_err(BackendError)
    }
    fn refusal(&self) -> Option<String> {
        self.broker.refusal.clone()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn spec() -> OutputSpec {
        OutputSpec {
            id: "12345678-1234-4234-8234-123456789abc".into(),
            name: "XR-1".into(),
            index: 0,
            width: 1920,
            height: 1080,
            refresh_millihz: 120000,
            scale: 1.0,
            x: 3840,
            y: 0,
        }
    }

    fn live() -> Value {
        json::parse_strict(
            br#"{"id":"12345678-1234-4234-8234-123456789abc","name":"XR-1","state":"live","physical":false,
            "fallback_usable":true,"observation":{"width":1920,"height":1080,"refresh_millihz":120000,"scale":1.0,
            "x":3840,"y":0,"workspace":61,"enabled":true,"dpms":true,"mirrored":false,"transform":0,
            "workspaces":[{"id":61,"name":"61","special":false}],"windows":[]}}"#,
        )
        .unwrap()
    }

    fn with(path: &[&str], v: Value) -> Value {
        fn set(at: &mut Value, path: &[&str], v: Value) {
            let Value::Object(entries) = at else { panic!() };
            let slot = &mut entries.iter_mut().find(|(k, _)| k == path[0]).unwrap().1;
            if path.len() == 1 {
                *slot = v
            } else {
                set(slot, &path[1..], v)
            }
        }
        let mut out = live();
        set(&mut out, path, v);
        out
    }

    #[test]
    fn a_readback_must_match_what_was_asked() {
        identity(&live(), &spec().id).unwrap();
        verify(&live(), &spec()).unwrap();
        let int = |n: i64| Value::Number(n.into());
        let cases = [
            (
                with(&["state"], Value::String("failed".into())),
                "creation incomplete",
            ),
            (with(&["observation"], Value::Null), "missing readback"),
            (
                with(&["observation", "x"], int(0)),
                "actual geometry/workspace mismatch",
            ),
            (
                with(&["observation", "dpms"], Value::Bool(false)),
                "unusable output",
            ),
            (
                with(&["observation", "workspace"], int(5)),
                "workspace placement mismatch: active 5 not among [61]",
            ),
            (
                with(&["observation", "workspaces"], Value::Array(vec![])),
                "workspace placement mismatch: expected regular workspaces observed []",
            ),
        ];
        for (reply, why) in cases {
            assert_eq!(verify(&reply, &spec()).unwrap_err(), why);
        }
        assert_eq!(
            identity(&with(&["physical"], Value::Bool(true)), &spec().id).unwrap_err(),
            "unproven broker identity"
        );
        assert_eq!(
            identity(&with(&["state"], Value::String("odd".into())), &spec().id).unwrap_err(),
            "invalid lifecycle"
        );
    }

    #[test]
    fn a_refused_removal_says_why() {
        let broker = Broker::new("r1", "s1", "DP-1").unwrap();
        let reply = with(&["fallback_usable"], Value::Bool(false));
        assert_eq!(
            broker.refused(&reply),
            "the ownership plugin refused to remove it (state live); \
             DP-1 cannot take its windows: it is disabled, mirrored or unplugged"
        );
        let mut stuck = with(&["state"], Value::String("blocked".into()));
        let Value::Object(entries) = &mut stuck else {
            panic!()
        };
        entries.push(("blocked".into(), Value::String("evacuate".into())));
        assert!(
            broker
                .refused(&stuck)
                .starts_with("its windows and workspaces could not be moved off it")
        );
    }
}
