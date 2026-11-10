//! Playback state as mpv reports it: only property observations and command replies change it
//! (spike/src/video_readback.hpp). The player feeds it straight from its event loop.

use serde_json::{Value, json};

#[derive(Clone, Debug, Default, PartialEq)]
pub struct Readback {
    pub paused: Option<bool>,
    pub deband: Option<bool>,
    pub position: Option<f64>,
    pub duration: Option<f64>,
    pub revision: u64,
    pub command_replies: u64,
    pub command_errors: u64,
    pub command_error: String,
}

impl Readback {
    pub fn flag(&mut self, name: &str, value: bool) {
        match name {
            "pause" => self.paused = Some(value),
            "deband" => self.deband = Some(value),
            _ => return,
        }
        self.revision += 1;
    }

    pub fn number(&mut self, name: &str, value: f64) {
        if !value.is_finite() {
            return;
        }
        match name {
            "time-pos" => self.position = Some(value.max(0.0)),
            "duration" => self.duration = Some(value.max(0.0)),
            _ => return,
        }
        self.revision += 1;
    }

    /// A command or set-property reply; `detail` is mpv's error string (None: unknown).
    pub fn reply(&mut self, success: bool, detail: Option<&str>) {
        self.command_replies += 1;
        if success {
            return;
        }
        self.command_errors += 1;
        self.command_error = detail
            .unwrap_or("native command failed")
            .bytes()
            .filter(|b| (32..127).contains(b))
            .take(256)
            .map(char::from)
            .collect();
    }

    /// A new file: nothing known about it yet (deband is a player setting and stays).
    pub fn restart(&mut self) {
        self.paused = None;
        self.position = None;
        self.duration = None;
        self.revision += 1;
    }

    /// Keys sorted, as nlohmann::json writes them.
    pub fn json(&self) -> Value {
        // Written in nlohmann's sorted order so it holds whichever map serde_json was built with.
        json!({
            "command_error": self.command_error,
            "command_errors": self.command_errors,
            "command_replies": self.command_replies,
            "deband_applied": self.deband,
            "duration_s": self.duration,
            "paused": self.paused,
            "position_s": self.position,
            "revision": self.revision,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn observed_state_only() {
        let mut r = Readback::default();
        r.flag("pause", true);
        r.flag("other", true);
        r.number("time-pos", -1.0);
        r.number("duration", f64::NAN);
        r.number("duration", 3.0);
        assert_eq!(
            (r.paused, r.position, r.duration, r.revision),
            (Some(true), Some(0.0), Some(3.0), 3)
        );
        r.reply(true, None);
        r.reply(false, Some("bad\n\"arg\"\u{7f}"));
        assert_eq!(
            (
                r.command_replies,
                r.command_errors,
                r.command_error.as_str()
            ),
            (2, 1, "bad\"arg\"")
        );
        r.reply(false, None);
        assert_eq!(r.command_error, "native command failed");
        r.flag("deband", true);
        r.restart();
        assert_eq!((r.paused, r.position, r.deband), (None, None, Some(true)));
        assert_eq!(
            r.json().to_string(),
            r#"{"command_error":"native command failed","command_errors":2,"command_replies":3,"deband_applied":true,"duration_s":null,"paused":null,"position_s":null,"revision":5}"#
        );
    }
}
