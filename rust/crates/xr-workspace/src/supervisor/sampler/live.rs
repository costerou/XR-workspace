//! The sampler's real client: the run's [`hyprland::Client`], with every query sent on a [`hyprland::Lane`] once one
//! request verified the compositor (see [`super`] for what that saves per sample).
//!
//! The first query of a run, and the first after any failure, goes through [`hyprland::Client::query`] with every
//! check; the lane is then taken from the identity that request verified. A query that fails on the lane drops it, so
//! the next one is a full request again. Evals (the binds, the warps) are rare and always take the full path.

use std::time::Duration;

use xr_wire::json;

use super::{CallError, SamplerClient};
use crate::hyprland::{self, Lane, Query};
use crate::lua::BindCommand;
use crate::py::to_serde;
use crate::supervisor::columns::ColumnsClient;
use crate::supervisor::overview_cursor::CursorClient;

/// [`SamplerClient`] over the run's Hyprland client. `bind_args` is the Python's bind tuple: the five strings
/// `(python, client, control_socket, run_id, session)` of a [`BindCommand`]; any other shape fails each bind call, as
/// the Python's `TypeError` does.
pub struct Live {
    client: hyprland::Client,
    lane: Option<Lane>,
}

impl Live {
    pub fn new(client: hyprland::Client) -> Self {
        Live { client, lane: None }
    }

    /// The Hyprland client back, at run end.
    pub fn into_inner(self) -> hyprland::Client {
        self.client
    }

    /// `kind`, on the lane when there is one, otherwise on a full request that then opens one.
    fn query_raw(&mut self, kind: Query, timeout: Duration) -> Result<json::Value, CallError> {
        if let Some(lane) = &mut self.lane {
            match lane.query(kind, timeout) {
                Ok(v) => return Ok(v),
                Err(_) => {
                    self.lane = None;
                    return Err(CallError::Failed);
                }
            }
        }
        let v = self.client.query(kind, timeout)?;
        self.lane = self.client.lane().ok();
        Ok(v)
    }
}

/// The bind command `bind_args` names, or the refusal of a malformed tuple.
fn command(bind_args: &[String]) -> Result<BindCommand<'_>, CallError> {
    match bind_args {
        [python, client, control_socket, run_id, session] => Ok(BindCommand {
            python,
            client,
            control_socket,
            run_id,
            session,
        }),
        _ => Err(CallError::Failed),
    }
}

/// A `cursorpos` reply's `x` and `y` (as [`super::cursor_reply`]), read from the strict reply without converting it.
fn cursor(pos: &json::Value) -> Option<(f64, f64)> {
    let number = |v: Option<&json::Value>| match v? {
        json::Value::Number(n) => n.as_f64().filter(|f| f.is_finite()),
        _ => None,
    };
    Some((number(pos.get("x"))?, number(pos.get("y"))?))
}

impl CursorClient for Live {
    fn hide_overview_cursor(&mut self) -> Result<(), CallError> {
        Ok(self.client.hide_overview_cursor()?)
    }

    fn show_overview_cursor(&mut self) -> Result<(), CallError> {
        Ok(self.client.show_overview_cursor()?)
    }
}

impl ColumnsClient for Live {
    fn query(&mut self, kind: Query, timeout: Duration) -> Result<serde_json::Value, CallError> {
        Ok(to_serde(&self.query_raw(kind, timeout)?))
    }

    fn scroll_to_window(&mut self, address: &str) -> Result<(), CallError> {
        Ok(self.client.scroll_to_window(address)?)
    }
}

impl SamplerClient for Live {
    fn cursor_pos(&mut self, timeout: Duration) -> Result<Option<(f64, f64)>, CallError> {
        Ok(cursor(&self.query_raw(Query::CursorPos, timeout)?))
    }

    fn grab_pointer(&mut self, bind_args: &[String], x: i64, y: i64) -> Result<(), CallError> {
        Ok(self.client.grab_pointer(&command(bind_args)?, x, y)?)
    }

    fn lock_pointer(&mut self, bind_args: &[String], x: i64, y: i64) -> Result<(), CallError> {
        Ok(self.client.lock_pointer(&command(bind_args)?, x, y)?)
    }

    fn release_pointer(&mut self, x: Option<i64>, y: Option<i64>) -> Result<(), CallError> {
        Ok(self.client.release_pointer(x, y)?)
    }

    fn warp_cursor(&mut self, x: i64, y: i64) -> Result<(), CallError> {
        Ok(self.client.warp_cursor(x, y)?)
    }

    fn remove_resize_binds(&mut self) -> Result<(), CallError> {
        Ok(self.client.remove_resize_binds()?)
    }

    fn enter_desktop(
        &mut self,
        bind_args: &[String],
        x: Option<i64>,
        y: Option<i64>,
        monitor: Option<&str>,
    ) -> Result<(), CallError> {
        Ok(self.client.enter_desktop(&command(bind_args)?, x, y, monitor)?)
    }

    fn arm_click_binds(&mut self, bind_args: &[String]) -> Result<(), CallError> {
        Ok(self.client.arm_click_binds(&command(bind_args)?)?)
    }

    fn confine_cursor(&mut self, x: i64, y: i64, name: &str) -> Result<(), CallError> {
        Ok(self.client.confine_cursor(x, y, name)?)
    }

    fn scrolls(&self) -> bool {
        true
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cursor_reads_as_cursor_reply() {
        for raw in [
            r#"{"x": 3, "y": 4.5}"#,
            r#"{"x": true, "y": 4}"#,
            r#"{"x": 3}"#,
            r#"[3, 4]"#,
            r#"{"x": "3", "y": 4}"#,
            r#"{"y": 2, "x": -7.25}"#,
        ] {
            let strict = json::parse_strict(raw.as_bytes()).unwrap();
            let loose: serde_json::Value = serde_json::from_str(raw).unwrap();
            assert_eq!(cursor(&strict), super::super::cursor_reply(&loose), "{raw}");
            assert_eq!(to_serde(&strict), loose, "{raw}");
        }
    }

    #[test]
    fn bind_tuples() {
        let five: Vec<String> = ["p", "c", "s", "r", "x"].map(String::from).to_vec();
        let cmd = command(&five).unwrap();
        assert_eq!((cmd.python, cmd.session), ("p", "x"));
        assert_eq!(command(&five[..4]).err(), Some(CallError::Failed));
        assert_eq!(command(&[]).err(), Some(CallError::Failed));
    }
}
