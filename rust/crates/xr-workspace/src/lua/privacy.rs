//! privacy.py: the main display dark while the glasses are on.

use super::{Refused, Result, connector};

pub const WAKE_ALL_LUA: &str = r#"hl.dispatch(hl.dsp.dpms({action="on"}))"#;

/// `(DP|HDMI-A|eDP|DVI-D)-[0-9]{1,3}`: supervisor.validate_physical_pair's connectors.
fn name(n: &str) -> Result<&str> {
    if connector(n, &["DP", "HDMI-A", "eDP", "DVI-D"], 1, 3) {
        Ok(n)
    } else {
        Err(Refused("invalid physical output name"))
    }
}

/// Main display off, then the (already on) glasses' output on so input does not wake it; both must exist.
pub fn blank_lua(target: &str, glasses: &str) -> Result<String> {
    let (t, g) = (name(target)?, name(glasses)?);
    if t == g {
        return Err(Refused("the main display cannot be the glasses"));
    }
    Ok(format!(
        "local t,g=\"{t}\",\"{g}\" local a,b=false,false \
         for _,m in ipairs(hl.get_monitors()) do if m.name==t then a=true elseif m.name==g then b=true end end \
         if a and b then hl.dispatch(hl.dsp.dpms({{action=\"off\",monitor=t}})) \
         hl.dispatch(hl.dsp.dpms({{action=\"on\",monitor=g}})) end"
    ))
}

/// The display back on, only if it exists (an unresolved selector would apply to every monitor).
pub fn wake_lua(target: &str) -> Result<String> {
    let t = name(target)?;
    Ok(format!(
        "local t=\"{t}\" for _,m in ipairs(hl.get_monitors()) do if m.name==t then \
         hl.dispatch(hl.dsp.dpms({{action=\"on\",monitor=t}})) break end end"
    ))
}
