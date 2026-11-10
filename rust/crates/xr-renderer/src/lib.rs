//! XR renderer on GLES and Wayland.
//!
//! The renderer's logic (layout checks, the view animator, glass geometry, HUD and panel layout) is ported from the
//! C++ renderer in `spike/`. It is pure maths and state machines with no GL, so it is tested on its own and its
//! geometry dumps are compared byte for byte with the C++ ones.

pub mod binds_panel;
pub mod cfmt;
pub mod depth;
pub mod depth_feed;
pub mod depth_link;
pub mod desktops;
pub mod egl;
pub mod gl;
pub mod glass;
pub mod glass_theme;
pub mod hud;
pub mod hud_gl;
pub mod ipc;
pub mod layout;
pub mod material;
pub mod options_geometry;
pub mod pose;
pub mod stereo;
pub mod stereo_gl;
pub mod video;
pub mod view;
