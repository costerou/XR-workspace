//! The command line, as `spike/driver/src/daemon.cpp` parses it: the same options, defaults and refusals, word for word.

use crate::daemon::DaemonConfig;
use std::time::Duration;

/// What the renderer in spike/src opens today.
pub const LIVE_IPC_PREFIX: &str = "/tmp/shader_runtime_";

pub const USAGE: &str = "usage: xr-luma-driver [options]
  --ipc-prefix PATH        where the pose feed goes (default /tmp/shader_runtime_, what the renderer reads)
  --replay FILE            run a capture instead of the glasses; needs --ipc-prefix, touches no USB device
  --replay-realtime        replay at recorded speed instead of as fast as it goes
  --stale-ms N             no samples this long: mark the feed stale (default 100)
  --reopen-ms N            no samples this long: close and reopen the device (default 1000)
  --keep-calibration-ms N  back within this long after a drop: keep the calibration (default 300000)
  --settle-ms N            leave a device that just came back alone this long before any command (default 200)
  --no-keep-heading        after a new calibration, start from its own heading instead of the last one
  --no-stop-command        leave the stream on at exit (the Ultra has no known stop, so this changes nothing there yet)
  --start MODE             listen: send nothing. stream: the start alone (default). prepared: 0x97 then the start.
                           full: 0x100B, 0x97, then the start, as VITURE's own software does
  --read-events            also read endpoint 0x83, and throw it away
  --drain-camera           also read camera endpoint 0x82 and throw it away: 19 MB/s, for a device that stalls without it
  --no-rt                  leave scheduling alone
  --rt-cpu N               pin to CPU N, but only once SCHED_FIFO is granted (default: not pinned)
  --state-dir PATH         where the gyro bias is kept between runs
  --pose-log FILE          replay only: t_ns, then w x y z, as five doubles, for every Nth pose
  --pose-log-every N       default 10
  --latency-log FILE       host and device time of every IMU packet, live or replay; read it with luma_latency_report
  --feature-NAME, --no-feature-NAME  optional features that send nothing; see spike/driver/docs/features.md
  --help
";

/// listen: send nothing. stream: FA 55 95 alone. prepared: 97 then 95. full: 0x100B, 97, 95, the order VITURE's own
/// software uses. See spike/driver/docs/live-test-plan.md, the start ladder.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Start {
    Listen,
    #[default]
    Stream,
    Prepared,
    Full,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Options {
    pub ipc_prefix: String,
    pub replay: Option<String>,
    pub replay_realtime: bool,
    pub help: bool,
    pub stop_command: bool,
    pub start: Start,
    /// Also read 0x83, and throw it away.
    pub read_events: bool,
    /// Also read 0x82 and throw it away: 19 MB/s, only if a live test shows the need.
    pub drain_camera: bool,
    /// Leave the thread's scheduling alone.
    pub no_rt: bool,
    /// Pin to this CPU, but only once SCHED_FIFO is granted.
    pub rt_cpu: Option<usize>,
    /// Where the gyro bias is kept between runs; empty: $XDG_STATE_HOME or ~/.local/state.
    pub state_dir: String,
    /// --replay only: write every Nth pose here, for tools.
    pub pose_log: Option<String>,
    pub pose_log_every: u32,
    /// Host and device time of every IMU packet, for luma_latency_report.
    pub latency_log: Option<String>,
    /// --feature-* and --no-feature-*, passed on as they are.
    pub feature_flags: Vec<String>,
    pub config: DaemonConfig,
}

impl Default for Options {
    fn default() -> Self {
        Options {
            ipc_prefix: LIVE_IPC_PREFIX.to_owned(),
            replay: None,
            replay_realtime: false,
            help: false,
            stop_command: true,
            start: Start::Stream,
            read_events: false,
            drain_camera: false,
            no_rt: false,
            rt_cpu: None,
            state_dir: String::new(),
            pose_log: None,
            pose_log_every: 10,
            latency_log: None,
            feature_flags: Vec::new(),
            config: DaemonConfig::default(),
        }
    }
}

/// A whole number of ms, 1 to 3600000.
fn parse_ms(text: &str) -> Option<Duration> {
    if text.is_empty() || !text.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    let value: u64 = text.parse().ok()?;
    (1..=3_600_000)
        .contains(&value)
        .then(|| Duration::from_millis(value))
}

/// `Err` says what is wrong with the command line (the arguments after the program name). A replay must name its
/// --ipc-prefix: run with the default it would overwrite the feed of a live session.
pub fn parse_options<S: AsRef<str>>(args: &[S]) -> Result<Options, String> {
    let mut o = Options::default();
    let mut prefix_given = false;
    let mut args = args.iter().map(AsRef::as_ref);
    while let Some(arg) = args.next() {
        let mut value = || {
            args.next()
                .map(str::to_owned)
                .ok_or_else(|| format!("{arg} needs a value"))
        };
        let mut number = || {
            let v = value()?;
            parse_ms(&v).ok_or_else(|| format!("{arg} needs a whole number of ms, 1 to 3600000"))
        };
        match arg {
            "--help" | "-h" => o.help = true,
            "--replay-realtime" => o.replay_realtime = true,
            "--no-keep-heading" => o.config.keep_heading = false,
            "--no-stop-command" => o.stop_command = false,
            "--read-events" => o.read_events = true,
            "--drain-camera" => o.drain_camera = true,
            "--no-rt" => o.no_rt = true,
            "--rt-cpu" => {
                o.rt_cpu = Some(
                    value()?
                        .parse::<usize>()
                        .ok()
                        .filter(|&cpu| cpu < 1024)
                        .ok_or("--rt-cpu needs a CPU number, 0 to 1023")?,
                )
            }
            "--state-dir" => o.state_dir = value()?,
            "--pose-log" => o.pose_log = Some(value()?),
            "--latency-log" => o.latency_log = Some(value()?),
            "--pose-log-every" => o.pose_log_every = number()?.as_millis() as u32,
            "--start" => {
                o.start = match value()?.as_str() {
                    "listen" => Start::Listen,
                    "stream" => Start::Stream,
                    "prepared" => Start::Prepared,
                    "full" => Start::Full,
                    _ => return Err("--start needs listen, stream, prepared or full".to_owned()),
                }
            }
            "--ipc-prefix" => {
                o.ipc_prefix = value()?;
                prefix_given = true;
            }
            "--replay" => o.replay = Some(value()?),
            "--stale-ms" => o.config.stale_after = number()?,
            "--reopen-ms" => o.config.silent_reopen_after = number()?,
            "--keep-calibration-ms" => o.config.calibration_keep = number()?,
            "--settle-ms" => o.config.settle_first = number()?,
            _ if arg.starts_with("--feature-") || arg.starts_with("--no-feature-") => {
                o.feature_flags.push(arg.to_owned())
            }
            _ => return Err(format!("unknown option: {arg}")),
        }
    }
    if o.ipc_prefix.is_empty() {
        return Err("--ipc-prefix must not be empty".to_owned());
    }
    if o.replay.is_some() && !prefix_given {
        return Err("--replay needs its own --ipc-prefix: the default is the live feed".to_owned());
    }
    if o.replay.is_some() && o.ipc_prefix == LIVE_IPC_PREFIX {
        return Err("--replay must not write the live feed".to_owned());
    }
    if o.replay_realtime && o.replay.is_none() {
        return Err("--replay-realtime needs --replay".to_owned());
    }
    if o.pose_log.is_some() && o.replay.is_none() {
        return Err("--pose-log needs --replay".to_owned());
    }
    o.config.settle_max = o.config.settle_max.max(o.config.settle_first);
    if o.config.silent_reopen_after <= o.config.stale_after {
        return Err("--reopen-ms must be longer than --stale-ms".to_owned());
    }
    Ok(o)
}
