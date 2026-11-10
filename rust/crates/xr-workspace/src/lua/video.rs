//! video.py: the VR video player's keys while a video plays.

use super::{BindCommand, Result, view};

pub const TABLE: &str = "xr_workspace_video_binds";
pub const VIDEO_BINDS: [(&str, &str); 10] = [
    ("space", "video_pause"),
    ("left", "video_seek_back"),
    ("right", "video_seek_fwd"),
    ("SHIFT + left", "video_seek_back_big"),
    ("SHIFT + right", "video_seek_fwd_big"),
    ("Tab", "video_format"),
    ("up", "video_up"),
    ("down", "video_down"),
    ("Escape", "video_exit"),
    ("q", "video_quality"),
];

pub fn install_video_binds_lua(cmd: &BindCommand) -> Result<String> {
    let base = cmd.base()?;
    Ok(format!(
        "{} {}",
        remove_video_binds_lua(),
        view::binds_lua(TABLE, &VIDEO_BINDS, &base)
    ))
}

pub fn remove_video_binds_lua() -> String {
    view::remove_lua(TABLE)
}
