//! Level bars and the levelling roll, and the roll geometry line for the supervisor's roll-level icons (wayland.cpp
//! set_roll, take_roll_geometry, resend_roll_geometry, and the render path's glass `bars` lines).

use xr_renderer::glass::LevelSegment;
use xr_renderer::view::{Canvas, FRect, Placement, Vec3, desktop_tilt};

use super::Presentation;
use super::scene::Scene;

/// The desktops a `roll` frame carries (ipc.hpp kMaxDesktops).
const MAX_DESKTOPS: usize = 4;

/// At most this often a changed roll geometry line goes out once it holds measurements (Presentation::kRollGeometryMs).
pub const ROLL_GEOMETRY_MS: f64 = 100.0;

/// What a measured roll geometry line is built from (take_roll_geometry's `measured` branch): the overview layout of
/// the settled sizes at the target spacing, each desktop's target placement (its roll is left out) and the world's up in
/// the canvas frame (pose.rs canvas_up of the level reference and the anchor).
pub(super) struct RollMeasure {
    pub tiles: Vec<FRect>,
    pub placements: Vec<Placement>,
    pub up: [f32; 3],
}

pub(super) struct RollState {
    /// Each desktop's levelling roll, hundredths of a degree (`set_roll`).
    rolls: [i32; MAX_DESKTOPS],
    bars: [bool; MAX_DESKTOPS],
    hover: i32,
    side: i8,
    active: bool,
    /// `set_roll` calls (the exit report's `view.roll_updates`).
    updates: u64,
    /// The canvas of the last GPU frame (None before one, or with no FOV): take_roll_geometry's `!half_w`.
    canvas: Option<Canvas>,
    /// The last line handed to the supervisor, when, and whether it held measurements (then changes are rate-limited).
    sent: String,
    sent_ms: f64,
    sent_measured: bool,
}

impl Default for RollState {
    fn default() -> Self {
        Self {
            rolls: [0; MAX_DESKTOPS],
            bars: [false; MAX_DESKTOPS],
            hover: -1,
            side: -1,
            active: false,
            updates: 0,
            canvas: None,
            sent: String::new(),
            sent_ms: -1e9,
            sent_measured: false,
        }
    }
}

impl RollState {
    /// Desktop `i`'s levelling roll, radians (wayland.cpp target_placement's roll); 0 past the four.
    pub(super) fn target_roll(&self, i: usize) -> f32 {
        self.rolls
            .get(i)
            .map_or(0.0, |&r| r as f32 / 100.0 * std::f32::consts::PI / 180.0)
    }

    /// The rolls as set, hundredths of a degree (continuity save: `c.rolls = s.rolls`). A change by `set_roll` ends a
    /// placement glide (it aims at the placements it began with, so it eases on from where it got to).
    #[allow(dead_code)] // continuity and scene_layout's glide read it once wired
    pub(super) fn rolls(&self) -> [i32; MAX_DESKTOPS] {
        self.rolls
    }

    /// Continuity restore (`s.rolls = c.rolls`): the rolls alone, a plain assignment. Unlike `set_roll` it neither
    /// resets the glide nor counts an update, and leaves the bars as they are; the restore invalidates the frame.
    #[allow(dead_code)] // continuity's restore calls it once wired
    pub(super) fn set_rolls(&mut self, rolls: [i32; MAX_DESKTOPS]) {
        self.rolls = rolls;
    }

    #[allow(dead_code)] // scene_layout's `view` report reads it once wired
    pub(super) fn updates(&self) -> u64 {
        self.updates
    }

    /// Each desktop's roll on `scene.own[i].roll`; the bars, hover, segment and active of `scene.glass()`.
    pub(super) fn contribute(&mut self, scene: &mut Scene) {
        self.canvas = (scene.half_w != 0.0)
            .then(|| Canvas::new(scene.width as f32, scene.height as f32, scene.half_w, scene.half_h));
        for (i, own) in scene.own.iter_mut().enumerate() {
            own.roll = self.target_roll(i);
        }
        let glass = &mut scene.glass().frame;
        for (bar, &on) in glass.bars.iter_mut().zip(&self.bars) {
            *bar = on;
        }
        glass.bar_hover = self.hover;
        glass.bar_segment = segment(self.side);
        glass.bar_active = self.active;
    }

    pub(super) fn animating(&self, _now_ms: f64) -> bool {
        false
    }

    /// take_roll_geometry past its `gl`/captures/FOV test: the line for `desktops` when it changed, rate-limited while
    /// measured.
    fn take_geometry(
        &mut self,
        now_ms: f64,
        canvas: &Canvas,
        desktops: usize,
        measure: Option<&RollMeasure>,
    ) -> Option<String> {
        let measured = measure.is_some();
        if measured && self.sent_measured && now_ms - self.sent_ms < ROLL_GEOMETRY_MS {
            return None; // rate limit
        }
        let line = roll_geometry_line(canvas, desktops, measure);
        if line == self.sent {
            return None;
        }
        self.sent.clone_from(&line);
        self.sent_ms = now_ms;
        self.sent_measured = measured;
        Some(line)
    }
}

/// The glass LevelSegment of a `roll` side (-1 none, 0 ccw, 1 level, 2 cw).
fn segment(side: i8) -> LevelSegment {
    match side {
        0 => LevelSegment::Ccw,
        1 => LevelSegment::Level,
        2 => LevelSegment::Cw,
        _ => LevelSegment::None,
    }
}

/// `{"roll_geometry":{"tilt":[...]}}`: each desktop's tilt from level as you see it without its levelling roll,
/// hundredths of a degree within ±18000, `null` where it is not measured (no `measure`, or desktop_tilt has none).
pub(super) fn roll_geometry_line(canvas: &Canvas, desktops: usize, measure: Option<&RollMeasure>) -> String {
    let mut line = String::from("{\"roll_geometry\":{\"tilt\":[");
    for i in 0..desktops {
        let tilt = measure.filter(|m| i < m.tiles.len()).and_then(|m| {
            let mut p = m.placements.get(i).copied().unwrap_or_default();
            p.roll = 0.0; // the supervisor adds its own roll
            desktop_tilt(canvas, &m.tiles[i], &p, &Vec3::new(m.up[0], m.up[1], m.up[2]))
        });
        if i > 0 {
            line.push(',');
        }
        match tilt {
            Some(t) => {
                let hundredths = (t * 180.0 / std::f32::consts::PI * 100.0).round() as i32;
                line.push_str(&hundredths.clamp(-18000, 18000).to_string());
            }
            None => line.push_str("null"),
        }
    }
    line.push_str("]}}");
    line
}

impl Presentation {
    /// `roll` each desktop's levelling roll (1/100 degree, counter-clockwise); `bars` whose level bars show; `hover` /
    /// `side` the lit segment (-1: none; side -1 none, 0 ccw, 1 level, 2 cw), brightest while `active`.
    pub fn set_roll(&mut self, roll: &[i32; 4], bars: &[bool; 4], hover: i32, side: i8, active: bool) {
        let r = &mut self.state.features.roll;
        r.rolls = *roll;
        r.bars = *bars;
        r.hover = hover;
        r.side = side;
        r.active = active;
        r.updates += 1;
        self.state.capture.invalidate();
    }

    /// `{"roll_geometry":{"tilt":[...]}}` when it changed, at most every ROLL_GEOMETRY_MS.
    pub fn take_roll_geometry(&mut self, now_ms: f64) -> Option<String> {
        let desktops = self.state.capture.gpu_desktops();
        let measure = self.roll_measure();
        let roll = &mut self.state.features.roll;
        let canvas = roll.canvas.filter(|_| desktops > 0)?;
        roll.take_geometry(now_ms, &canvas, desktops, measure.as_ref())
    }

    /// The last take was not delivered.
    pub fn resend_roll_geometry(&mut self) {
        self.state.features.roll.sent.clear();
    }

    /// The measured branch's inputs, when the tilt is measured: tracking with a level reference, anchor mode settled
    /// and no VR video. Not measured in this build: the pose reference is the tracking module's, and until it is wired
    /// every tilt is `null`, as the C++ sends untracked.
    fn roll_measure(&self) -> Option<RollMeasure> {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use xr_renderer::pose::{Quat, canvas_up, quat_conj, quat_mul};
    use xr_renderer::view::{Size, overview_layout};

    const PI: f32 = 3.14159265358979;

    fn rad(deg: f32) -> f32 {
        deg * PI / 180.0
    }
    fn axis_angle(x: f32, y: f32, z: f32, degrees: f32) -> Quat {
        let n = (x * x + y * y + z * z).sqrt();
        let h = rad(degrees) / 2.0;
        let s = h.sin() / n;
        Quat {
            x: x * s,
            y: y * s,
            z: z * s,
            w: h.cos(),
        }
    }
    fn head_at(yaw: f32, pitch: f32, tip: f32) -> Quat {
        quat_mul(
            quat_mul(axis_angle(0.0, 0.0, 1.0, yaw), axis_angle(0.0, 1.0, 0.0, pitch)),
            axis_angle(1.0, 0.0, 0.0, tip),
        )
    }
    fn placement(x: f32, y: f32, distance: f32, curvature: f32, roll: f32) -> Placement {
        Placement {
            x,
            y,
            distance,
            curvature,
            roll,
        }
    }
    /// golden/roll_geometry.cpp's `line`: a fixed reference stands for PoseState::reference.
    #[allow(clippy::too_many_arguments)]
    fn line(
        c: &Canvas,
        desktops: usize,
        settled: &[Size],
        spacing: f32,
        placements: &[Placement],
        reference: Option<Quat>,
        anchor: Quat,
    ) -> String {
        let measure = reference.map(|r| RollMeasure {
            tiles: overview_layout(settled, c.view_w, c.view_h, spacing),
            placements: placements.to_vec(),
            up: canvas_up(r, anchor),
        });
        roll_geometry_line(c, desktops, measure.as_ref())
    }

    /// The supervisor's roll geometry lines, byte-equal with golden/roll_geometry.cpp's C++ over the same inputs.
    #[test]
    fn roll_geometry_lines_match_the_cpp() {
        let frame_canvas = Canvas::new(1920.0, 1200.0, 0.41421356, 0.25888);
        let hd = Canvas::new(1920.0, 1080.0, 0.41421356, 0.23300762);
        let three = vec![Size::new(1920.0, 1200.0); 3];
        let four = vec![Size::new(1920.0, 1080.0); 4];
        let mixed = [
            Size::new(2560.0, 1440.0),
            Size::new(1920.0, 1080.0),
            Size::new(1080.0, 1920.0),
        ];
        let placed = [
            placement(rad(0.7), rad(9.0), 0.33, 0.0, 0.0),
            placement(rad(34.5), rad(8.5), 0.31, 0.0, 0.0),
            placement(rad(-5.2), rad(-15.7), 0.69, 0.0, 0.0),
        ];
        let rolled = [
            placement(rad(30.0), rad(12.0), 1.4, 0.5, rad(20.0)),
            placement(0.0, 0.0, 1.0, 0.0, rad(-35.0)),
            placement(rad(140.0), rad(-5.0), 1.0, 0.0, rad(7.5)),
        ];
        let level = Quat::default();
        let mut out = String::new();
        let mut emit = |name: &str, l: String| out.push_str(&format!("case {name}\n{l}\n"));

        for n in 0..=4 {
            emit(&format!("unmeasured {n}"), line(&hd, n, &four, 20.0, &[], None, level));
        }
        let live = quat_conj(Quat {
            x: 0.539,
            y: 0.826,
            z: 0.163,
            w: -0.014,
        });
        let odd = [
            ("level", level),
            ("live", live),
            ("upside down on a desk", axis_angle(1.0, 0.0, 0.0, 180.0)),
            ("lying on one arm", axis_angle(1.0, 0.0, 0.0, 90.0)),
            ("hanging, lenses down", axis_angle(0.0, 1.0, 0.0, 88.0)),
            ("hanging, lenses up", axis_angle(0.0, 1.0, 0.0, -70.0)),
            ("tipped", head_at(5.0, 0.0, 12.0)),
            ("tipped and pitched", head_at(-30.0, 25.0, -20.0)),
            ("plausible", head_at(0.0, 20.0, 25.0)),
        ];
        for (name, reference) in odd {
            emit(
                &format!("placed, {name}"),
                line(&frame_canvas, 3, &three, 20.0, &placed, Some(reference), level),
            );
            emit(
                &format!("rolled, {name}"),
                line(&hd, 3, &mixed, 40.0, &rolled, Some(reference), level),
            );
            let anchor = head_at(35.0, -8.0, 0.0);
            emit(
                &format!("anchored, {name}"),
                line(&frame_canvas, 3, &three, 20.0, &placed, Some(reference), anchor),
            );
        }
        for head_roll in [-20.0f32, 20.0] {
            for pitch in [0.0f32, -20.0, 25.0] {
                let reference = quat_mul(axis_angle(0.0, 1.0, 0.0, pitch), axis_angle(1.0, 0.0, 0.0, head_roll));
                let name = format!("four, roll {} pitch {}", head_roll as i32, pitch as i32);
                emit(&name, line(&frame_canvas, 4, &four, 20.0, &[], Some(reference), level));
            }
        }
        let tip = Some(head_at(0.0, 0.0, 9.0));
        emit("fewer placements", line(&hd, 4, &four, 20.0, &placed[1..2], tip, level));
        emit("widest spacing", line(&hd, 4, &four, 400.0, &rolled, tip, level));
        let overhead = [placement(0.0, rad(89.5), 1.0, 0.0, 0.0), Placement::default()];
        emit("overhead", line(&hd, 2, &four, 20.0, &overhead, tip, level));
        emit(
            "upside down",
            line(&hd, 2, &four, 20.0, &[], Some(axis_angle(1.0, 0.0, 0.0, 179.99)), level),
        );
        emit(
            "half turn",
            line(&hd, 2, &four, 20.0, &[], Some(axis_angle(1.0, 0.0, 0.0, -180.0)), level),
        );

        let golden = include_str!("../../golden/roll_geometry.txt");
        assert!(golden.lines().filter(|l| l.starts_with("case ")).count() >= 40);
        if out != golden {
            let (c, r) = golden
                .lines()
                .zip(out.lines())
                .enumerate()
                .find(|(_, (a, b))| a != b)
                .map_or((golden.lines().count(), out.lines().count()), |(i, _)| (i + 1, i + 1));
            panic!(
                "differs from golden/roll_geometry.txt at line {c}/{r}:\n  C++:  {:?}\n  Rust: {:?}",
                golden.lines().nth(c - 1),
                out.lines().nth(r - 1)
            );
        }
    }

    /// ctest `level_frame`'s view half (spike/tests/level_frame_test.cpp `tilts` and `--dump`): the tilts the renderer
    /// reports for Cody's three placed desktops, against fixed references (the truth of its dump's scenarios). Its pose
    /// half, PoseState::reference learning those references from the feed, is the tracking module's twin.
    #[test]
    fn level_frame_truth_lines() {
        let c = Canvas::new(1920.0, 1200.0, 0.41421356, 0.25888);
        let three = [Size::new(1920.0, 1200.0); 3];
        let placed = [
            placement(rad(0.7), rad(9.0), 0.33, 0.0, 0.0),
            placement(rad(34.5), rad(8.5), 0.31, 0.0, 0.0),
            placement(rad(-5.2), rad(-15.7), 0.69, 0.0, 0.0),
        ];
        let tilts = |r: Quat| line(&c, 3, &three, 20.0, &placed, Some(r), Quat::default());
        // `level_frame_test --dump`: "truth":[0,0,0] worn level, [-1186,-987,-1239] for head_at(5, 0, 12).
        assert_eq!(tilts(Quat::default()), "{\"roll_geometry\":{\"tilt\":[0,0,0]}}");
        assert_eq!(
            tilts(head_at(5.0, 0.0, 12.0)),
            "{\"roll_geometry\":{\"tilt\":[-1186,-987,-1239]}}"
        );
        // A level reference under any heading reads level (glasses_worn_straight_at_the_start_keep_exact_gravity's truth).
        for heading in [0.0f32, 70.0, -130.0] {
            assert_eq!(
                tilts(axis_angle(0.0, 0.0, 1.0, heading)),
                "{\"roll_geometry\":{\"tilt\":[0,0,0]}}"
            );
        }
    }

    /// Continuity's save and restore carry the rolls alone: no update counted, the bars untouched.
    #[test]
    fn continuity_round_trips_the_rolls() {
        let mut s = RollState {
            bars: [true, false, false, true],
            hover: 1,
            side: 0,
            ..RollState::default()
        };
        s.set_rolls([-300, 0, 9000, -9000]);
        assert_eq!(s.rolls(), [-300, 0, 9000, -9000]);
        assert_eq!(s.updates(), 0);
        assert_eq!((s.bars, s.hover, s.side), ([true, false, false, true], 1, 0));
        assert_eq!(s.target_roll(2), 9000.0 / 100.0 * std::f32::consts::PI / 180.0);
    }

    fn hd_measure(up: [f32; 3]) -> RollMeasure {
        RollMeasure {
            tiles: overview_layout(&[Size::new(1920.0, 1080.0); 2], 1920.0, 1080.0, 20.0),
            placements: Vec::new(),
            up,
        }
    }

    /// Unmeasured lines go out on every change; measured ones at most every ROLL_GEOMETRY_MS after a measured one; the
    /// same line never twice, unless the last was not delivered.
    #[test]
    fn take_rate_limits_measured_lines_and_skips_repeats() {
        let c = Canvas::new(1920.0, 1080.0, 0.41421356, 0.23300762);
        let mut s = RollState::default();
        assert_eq!(
            s.take_geometry(0.0, &c, 2, None).as_deref(),
            Some("{\"roll_geometry\":{\"tilt\":[null,null]}}")
        );
        assert_eq!(s.take_geometry(1.0, &c, 2, None), None); // unchanged
        assert!(s.take_geometry(2.0, &c, 3, None).is_some()); // a desktop more, at once
        let level = canvas_up(Quat::default(), Quat::default());
        let tipped = canvas_up(axis_angle(1.0, 0.0, 0.0, 9.0), Quat::default());
        assert_eq!(
            s.take_geometry(3.0, &c, 2, Some(&hd_measure(level))).as_deref(),
            Some("{\"roll_geometry\":{\"tilt\":[0,0]}}")
        );
        assert_eq!(
            s.take_geometry(3.0 + ROLL_GEOMETRY_MS - 1.0, &c, 2, Some(&hd_measure(tipped))),
            None
        ); // too soon
        let later = s.take_geometry(3.0 + ROLL_GEOMETRY_MS, &c, 2, Some(&hd_measure(tipped)));
        assert!(later.is_some_and(|l| l != "{\"roll_geometry\":{\"tilt\":[0,0]}}"));
        assert_eq!(s.take_geometry(500.0, &c, 2, Some(&hd_measure(tipped))), None); // unchanged
        s.sent.clear(); // resend_roll_geometry
        assert!(s.take_geometry(500.0, &c, 2, Some(&hd_measure(tipped))).is_some());
        // Losing the measurement goes out at once, however soon.
        assert!(s.take_geometry(501.0, &c, 2, None).is_some());
    }

    /// set_roll's state reaches the frame: each desktop's roll in radians, the bars and the lit segment on the glass.
    #[test]
    fn contribute_puts_rolls_and_bars_on_the_frame() {
        let mut s = RollState {
            rolls: [-300, 0, 9000, -9000],
            bars: [true, false, true, true],
            hover: 2,
            side: 2,
            active: true,
            ..RollState::default()
        };
        let tiles = vec![FRect::default(); 3];
        let mut scene = Scene::new(0.0, (1920, 1080), (0.41421356, 0.23300762), tiles);
        s.contribute(&mut scene);
        let want: Vec<f32> = [-300i32, 0, 9000]
            .iter()
            .map(|&r| r as f32 / 100.0 * std::f32::consts::PI / 180.0)
            .collect();
        assert_eq!(scene.own.iter().map(|p| p.roll).collect::<Vec<_>>(), want);
        let g = &scene.glass.as_ref().unwrap().frame;
        assert_eq!(g.bars, [true, false, true, true]);
        assert_eq!((g.bar_hover, g.bar_segment, g.bar_active), (2, LevelSegment::Cw, true));
        assert_eq!(s.canvas, Some(Canvas::new(1920.0, 1080.0, 0.41421356, 0.23300762)));
        for (side, seg) in [
            (-1, LevelSegment::None),
            (0, LevelSegment::Ccw),
            (1, LevelSegment::Level),
            (2, LevelSegment::Cw),
        ] {
            assert_eq!(segment(side), seg);
        }
        // No FOV yet: no line (take_roll_geometry's `!half_w`).
        let mut scene = Scene::new(0.0, (1920, 1080), (0.0, 0.0), Vec::new());
        s.contribute(&mut scene);
        assert_eq!(s.canvas, None);
    }
}
