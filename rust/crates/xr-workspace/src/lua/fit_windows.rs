//! fit_windows.py: windows moved and resized to fit their desktop, BATCH per eval.

use super::{Refused, Result, address};

/// Windows in one eval: at the longest addresses and coordinates, still under the client's 4096-byte limit.
pub const BATCH: usize = 48;
pub const MAX_COORD: i64 = 2147483647;

/// One window's move: to (x, y), and to size (w, h) unless both are 0.
#[derive(Clone, Copy, Debug)]
pub struct Move<'a> {
    pub address: &'a str,
    pub x: i64,
    pub y: i64,
    pub w: i64,
    pub h: i64,
}

pub const LUA: &str = concat!(
    r#"for _,e in ipairs(M) do pcall(function() local s="address:"..e[1] if e[4]>0 then "#,
    r#"hl.dispatch(hl.dsp.window.resize({x=e[4],y=e[5],window=s})) end "#,
    r#"hl.dispatch(hl.dsp.window.move({x=e[2],y=e[3],window=s})) end) end"#,
);

/// The evals for `moves`, BATCH windows each.
pub fn lua(moves: &[Move]) -> Result<Vec<String>> {
    for m in moves {
        let size = |v: i64| (0..=MAX_COORD).contains(&v);
        if !address(m.address)
            || m.x.abs() > MAX_COORD
            || m.y.abs() > MAX_COORD
            || !size(m.w)
            || !size(m.h)
            || (m.w == 0) != (m.h == 0)
        {
            return Err(Refused("invalid window moves"));
        }
    }
    Ok(moves
        .chunks(BATCH)
        .map(|part| {
            let rows: Vec<String> = part
                .iter()
                .map(|m| format!("{{\"{}\",{},{},{},{}}}", m.address, m.x, m.y, m.w, m.h))
                .collect();
            format!("local M={{{}}} {LUA}", rows.join(","))
        })
        .collect())
}
