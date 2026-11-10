//! adopt.py: the physical monitor's windows onto the XR desktops at session start, workspace by workspace.

use super::{EVAL_LIMIT, Refused, Result, address, output};

pub const LUA_HEAD: &str = concat!(
    r#"local ow=type(o)=="table" and type(o.workspaces)=="table" and o.workspaces or nil local "#,
    r#"function free(b) for s=10,1,-1 do if not hl.get_workspace(b+s) then return b+s end end "#,
    r#"end local function home(id,name,s) if not ow or type(ow.workspace_id)~="function" then "#,
    r#"return end local m=hl.get_monitor(name) local w=hl.get_workspace(id) if not m or not w "#,
    r#"or not w.monitor or w.monitor.name~=name then return end local b=ow.workspace_id(m,1)-1 "#,
    r#"local inb=id>b and id<=b+10 local want=b+((s or id)-1)%10+1 if want==id or (inb and not "#,
    r#"s) then return end local x=hl.get_workspace(want) if x and (x.windows or 0)==0 and not "#,
    r#"(x.monitor and x.monitor.active_workspace and x.monitor.active_workspace.id==want) then "#,
    r#"local f=free(b) if f then "#,
    r#"hl.dispatch(hl.dsp.workspace.change_id({workspace=tostring(want),id=f})) end "#,
    r#"x=hl.get_workspace(want) end if x and inb then return end local n=(not x) and want or "#,
    r#"free(b) if n then hl.dispatch(hl.dsp.workspace.change_id({workspace=tostring(id),id=n})) "#,
    r#"end end "#,
);

const LUA_WINDOWS: &str = concat!(
    r#"for _,e in ipairs(C) do pcall(function() local m=hl.get_monitor(e[2]) local a=m and "#,
    r#"m.active_workspace if a and a.id>0 then "#,
    r#"hl.dispatch(hl.dsp.window.move({workspace=tostring(a.id),follow=false,window="address:"..e[1]})) "#,
    r#"end end) end "#,
);

const LUA_WORKSPACES: &str = concat!(
    r#"for _,e in ipairs(W) do pcall(function() "#,
    r#"hl.dispatch(hl.dsp.workspace.move({workspace=tostring(e[1]),monitor=e[2]})) local "#,
    r#"w=hl.get_workspace(e[1]) if w and w.monitor and w.monitor.name==e[2] then "#,
    r#"home(e[1],e[2]) end end) end"#,
);

/// The eval(s) for an adoption plan: workspaces `(id, output)` moved whole and windows `(address, output)` moved one by
/// one. One eval when it fits (windows first, so a window lands on its desktop's own workspace, then the workspaces),
/// else several, each at most EVAL_LIMIT. Empty when there is nothing to move.
pub fn lua(workspaces: &[(i64, &str)], windows: &[(&str, &str)]) -> Result<Vec<String>> {
    const BAD: Refused = Refused("invalid adoption plan");
    if workspaces
        .iter()
        .any(|&(id, name)| !(0 < id && id <= 2147483647) || !output(name))
    {
        return Err(BAD);
    }
    if windows
        .iter()
        .any(|&(a, name)| !address(a) || !output(name))
    {
        return Err(BAD);
    }
    let items: Vec<Item> = windows
        .iter()
        .map(|&m| Item::Window(m))
        .chain(workspaces.iter().map(|&m| Item::Workspace(m)))
        .collect();
    let mut out = Vec::new();
    let mut start = 0;
    for end in 0..items.len() {
        if end > start && chunk(&items[start..=end]).len() > EVAL_LIMIT {
            out.push(chunk(&items[start..end]));
            start = end;
        }
    }
    if start < items.len() {
        out.push(chunk(&items[start..]));
    }
    Ok(out)
}

#[derive(Clone, Copy)]
enum Item<'a> {
    Window((&'a str, &'a str)),
    Workspace((i64, &'a str)),
}

fn chunk(items: &[Item]) -> String {
    let c: Vec<String> = items
        .iter()
        .filter_map(|i| {
            if let Item::Window((a, n)) = i {
                Some(format!("{{\"{a}\",\"{n}\"}}"))
            } else {
                None
            }
        })
        .collect();
    let w: Vec<String> = items
        .iter()
        .filter_map(|i| {
            if let Item::Workspace((id, n)) = i {
                Some(format!("{{{id},\"{n}\"}}"))
            } else {
                None
            }
        })
        .collect();
    format!(
        "local C={{{}}} local W={{{}}} {LUA_HEAD}{LUA_WINDOWS}{LUA_WORKSPACES}",
        c.join(","),
        w.join(",")
    )
}
