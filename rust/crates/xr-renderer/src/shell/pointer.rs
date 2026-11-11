//! The overview free pointer and a window drag's ghost (wayland.cpp set_overview_pointer and the render path's
//! set_free_pointer / set_window_drag; `view.rs` PointerLook and the pointer constants).

use xr_renderer::view::{FREE_POINTER_HELD_LEVEL, FREE_POINTER_LEVEL, PointerLook};

use super::Presentation;
use super::scene::{DragInput, PointerInput, Scene};

/// The last `overview_pointer` op, as wayland.cpp's `free_pointer`.
#[derive(Default)]
pub(super) struct Pointer {
    visible: bool,
    x: i32,
    y: i32,
    held: bool,
    directed: bool,
    /// Hundredths of a degree.
    az: i32,
    el: i32,
    ghost_w: i32,
    ghost_h: i32,
    target: i32,
    look: PointerLook,
    /// Times it went from hidden to visible (the exit report's `shown`).
    shown: u64,
}

impl Pointer {
    /// `scene.pointer` and `scene.drag`, every frame (wayland.cpp 1896-1906).
    pub(super) fn contribute(&mut self, scene: &mut Scene) {
        // Overview only; world-locked on the canvas. The overview pan's landing shows it again: the real cursor is on
        // the target.
        let free_pointer = self.visible && (!scene.switching || scene.overview_pan_landing);
        // The hand's pan dot (scene_layout's pan_dot) takes the pointer's place while the edge pan runs: the core sets it
        // once the frame's placements are resolved (captures.rs draw_gpu).
        scene.pointer = Some(PointerInput {
            visible: free_pointer,
            x: self.x as f32,
            y: self.y as f32,
            level: if self.held { FREE_POINTER_HELD_LEVEL } else { FREE_POINTER_LEVEL },
            directed: self.directed,
            az: self.az as f32 / 100.0,
            el: self.el as f32 / 100.0,
            look: self.look,
        });
        scene.drag = Some(DragInput {
            w: self.ghost_w as f32,
            h: self.ghost_h as f32,
            target: if free_pointer { self.target } else { -1 },
        });
    }

    pub(super) fn animating(&self, _now_ms: f64) -> bool {
        false
    }

    /// The exit report's `overview_pointer` section.
    pub(super) fn report(&self, out: &mut String) {
        out.push_str(&format!(",\"overview_pointer\":{{\"shown\":{}}}", self.shown));
    }
}

impl Presentation {
    /// A dot at canvas (x, y), brighter while `held`; `directed`: at (az, el) in 1/100 degree instead. `ghost_w` /
    /// `ghost_h` > 0: a window drag's ghost, `target` >= 0 the desktop it would land on. `look`: the marks round it.
    #[allow(clippy::too_many_arguments)]
    pub fn set_overview_pointer(
        &mut self,
        visible: bool,
        x: i32,
        y: i32,
        held: bool,
        directed: bool,
        az: i32,
        el: i32,
        ghost_w: i32,
        ghost_h: i32,
        target: i32,
        look: PointerLook,
    ) {
        let p = &mut self.state.features.pointer;
        if visible && !p.visible {
            p.shown += 1;
        }
        let shown = p.shown;
        *p = Pointer { visible, x, y, held, directed, az, el, ghost_w, ghost_h, target, look, shown };
        // A held slider keeps the options panel on the placement it had at the press.
        self.state.features.menus.set_pointer_held(held);
        self.state.capture.invalidate();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn frame(p: &mut Pointer, switching: bool, landing: bool) -> Scene {
        let mut scene = Scene::new(0.0, (1920, 1200), (0.36, 0.225), Vec::new());
        scene.switching = switching;
        scene.overview_pan_landing = landing;
        p.contribute(&mut scene);
        scene
    }

    #[test]
    fn pointer_and_drag_every_frame() {
        let mut p = Pointer::default();
        let s = frame(&mut p, false, false);
        assert!(s.pointer.is_some_and(|d| !d.visible));
        assert!(s.drag.is_some_and(|d| d.target == -1));

        p = Pointer {
            visible: true,
            x: 700,
            y: 500,
            held: true,
            directed: true,
            az: -12345,
            el: 250,
            ghost_w: 80,
            ghost_h: 50,
            target: 2,
            look: PointerLook::Text,
            shown: 1,
        };
        let s = frame(&mut p, false, false);
        let d = s.pointer.unwrap();
        assert!(d.visible && d.directed && d.x == 700.0 && d.y == 500.0 && d.look == PointerLook::Text);
        assert_eq!((d.level, d.az, d.el), (FREE_POINTER_HELD_LEVEL, -12345.0 / 100.0, 2.5));
        let g = s.drag.unwrap();
        assert_eq!((g.w, g.h, g.target), (80.0, 50.0, 2));

        // A switch hides it (and the drop target) unless it is the overview pan's landing.
        let s = frame(&mut p, true, false);
        assert!(!s.pointer.unwrap().visible && s.drag.unwrap().target == -1);
        let s = frame(&mut p, true, true);
        assert!(s.pointer.unwrap().visible && s.drag.unwrap().target == 2);

        p.held = false;
        assert_eq!(frame(&mut p, false, false).pointer.unwrap().level, FREE_POINTER_LEVEL);
    }

    #[test]
    fn report_counts_shows() {
        let p = Pointer { shown: 3, ..Pointer::default() };
        let mut out = String::new();
        p.report(&mut out);
        assert_eq!(out, ",\"overview_pointer\":{\"shown\":3}");
    }
}
