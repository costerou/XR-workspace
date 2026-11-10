//! How many desktops a run has, their scales and sizes (`spike/include/xr/desktops.hpp`).
//!
//! Mirrors `tools/native_spike/geometry.py` and the broker's `validDesktop`; the glass contract compares them.

/// Most managed desktops a run can have (XR-1..XR-4): a glass pill's Add button stops there.
pub const MAX_DESKTOPS: usize = 4;

/// A desktop's Hyprland output scales in percent, the steps of a glass pill's Scale dropdown.
pub const SCALE_PERCENTS: [u16; 7] = [100, 125, 150, 175, 200, 250, 300];
pub const SCALE_STEPS: usize = SCALE_PERCENTS.len();

/// A desktop's pixel size bounds, both even.
pub const DESKTOP_MIN_W: u16 = 640;
pub const DESKTOP_MAX_W: u16 = 7680;
pub const DESKTOP_MIN_H: u16 = 480;
pub const DESKTOP_MAX_H: u16 = 4320;

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Resolution {
    pub width: u16,
    pub height: u16,
}

/// One column of the Resolution dropdown: its name and how many of [`RESOLUTIONS`], in order, are in it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ResolutionGroup {
    pub name: &'static str,
    pub count: usize,
}

const fn r(width: u16, height: u16) -> Resolution {
    Resolution { width, height }
}

pub const RESOLUTIONS: [Resolution; 12] = [
    r(1280, 720), r(1600, 900), r(1920, 1080), r(2560, 1440), r(3840, 2160),
    r(1680, 1050), r(1920, 1200), r(2560, 1600),
    r(2560, 1080), r(3440, 1440), r(3840, 1080), r(5120, 1440),
];
pub const RESOLUTION_COUNT: usize = RESOLUTIONS.len();
pub const RESOLUTION_GROUPS: [ResolutionGroup; 3] = [
    ResolutionGroup { name: "16:9", count: 5 },
    ResolutionGroup { name: "16:10", count: 3 },
    ResolutionGroup { name: "Ultrawide", count: 4 },
];
pub const RESOLUTION_GROUP_COUNT: usize = RESOLUTION_GROUPS.len();
/// Rows of the dropdown: the largest group.
pub const RESOLUTION_ROWS: usize = 5;

const _: () = {
    assert!(RESOLUTION_GROUPS[0].count + RESOLUTION_GROUPS[1].count + RESOLUTION_GROUPS[2].count == RESOLUTION_COUNT);
    assert!(RESOLUTION_GROUPS[0].count == RESOLUTION_ROWS);
    assert!(RESOLUTION_GROUPS[1].count <= RESOLUTION_ROWS && RESOLUTION_GROUPS[2].count <= RESOLUTION_ROWS);
};
