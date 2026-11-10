//! columns.py: bring a window's scrolling-layout column into view.

use super::{Refused, Result, address};

/// Focus window `address` with `cursor.no_warps` on for that one focus (the cursor stays at the edge), the option put
/// back as it was.
pub fn scroll_lua(addr: &str) -> Result<String> {
    if !address(addr) {
        return Err(Refused("invalid window address"));
    }
    Ok(format!(
        "do local ok, w = pcall(hl.get_config, \"cursor.no_warps\") pcall(hl.config, {{ cursor = {{ no_warps = true }} }}) \
         pcall(function() hl.dispatch(hl.dsp.focus({{ window = \"address:{addr}\" }})) end) \
         pcall(hl.config, {{ cursor = {{ no_warps = ok and w == true }} }}) end"
    ))
}
