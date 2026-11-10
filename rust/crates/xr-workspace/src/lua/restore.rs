//! restore.py: the physical monitor's arrangement put back at Stop and Recover, in one eval.

use super::{Refused, Result, address, token};

pub const EVAL_LIMIT: usize = 16384;
pub const COORD: i64 = 1 << 20;

/// A snapshot as restore.capture makes it.
#[derive(Clone, Debug)]
pub struct Snapshot {
    pub schema: i64,
    pub run_id: String,
    pub monitor: String,
    pub active: Option<i64>,
    pub focus: Option<String>,
    pub focus_monitor: Option<String>,
    /// `(id, whole)`.
    pub workspaces: Vec<(i64, bool)>,
    pub windows: Vec<Window>,
}

#[derive(Clone, Debug)]
pub struct Window {
    pub address: String,
    pub workspace: i64,
    pub floating: bool,
    pub at: (i64, i64),
    pub size: (i64, i64),
    pub fullscreen: i64,
    pub client: i64,
}

fn within(v: i64, low: i64, high: i64) -> bool {
    (low..=high).contains(&v)
}

/// The snapshot if it has exactly the shape capture() makes, else refused (the first failing check's message).
pub fn validate(s: &Snapshot) -> Result<()> {
    if s.schema != 1 {
        return Err(Refused("invalid layout snapshot"));
    }
    if !token(&s.run_id) {
        return Err(Refused("invalid identifier"));
    }
    if !token(&s.monitor) || s.monitor.starts_with("XR-") {
        return Err(Refused("invalid layout monitor"));
    }
    if s.active.is_some_and(|a| !within(a, 1, 2147483647)) {
        return Err(Refused("invalid active workspace"));
    }
    if s.focus.as_deref().is_some_and(|f| !address(f)) {
        return Err(Refused("invalid focus"));
    }
    if s.focus_monitor.as_deref().is_some_and(|m| !token(m)) {
        return Err(Refused("invalid focus monitor"));
    }
    let mut seen = Vec::new();
    for &(id, _) in &s.workspaces {
        if !within(id, 1, 2147483647) || seen.contains(&id) {
            return Err(Refused("invalid workspace"));
        }
        seen.push(id);
    }
    let mut addresses: Vec<&str> = Vec::new();
    for w in &s.windows {
        let pair = |p: (i64, i64)| within(p.0, -COORD, COORD) && within(p.1, -COORD, COORD);
        if !address(&w.address)
            || addresses.contains(&w.address.as_str())
            || !seen.contains(&w.workspace)
            || !pair(w.at)
            || !pair(w.size)
            || w.size.0.min(w.size.1) < 0
            || !within(w.fullscreen, 0, 3)
            || !within(w.client, 0, 3)
        {
            return Err(Refused("invalid window"));
        }
        addresses.push(&w.address);
    }
    Ok(())
}

pub const LUA: &str = concat!(
    r#"local ow=type(o)=="table" and type(o.workspaces)=="table" and o.workspaces or nil local "#,
    r#"function D(f,a) pcall(function() hl.dispatch(f(a)) end) end local function ws(id) return "#,
    r#"hl.get_workspace(id) end local function win(a) return hl.get_window("address:"..a) end "#,
    r#"local function ren(a,b) D(hl.dsp.workspace.change_id,{workspace=tostring(a),id=b}) end "#,
    r#"local T={} for _,s in ipairs(S) do T[s[1]]=s end if not hl.get_monitor(F) then return "#,
    r#"end local function block(m) if not ow or type(ow.workspace_id)~="function" or not m then "#,
    r#"return nil end local ok,v=pcall(ow.workspace_id,m,1) if ok and type(v)=="number" then "#,
    r#"return v-1 end end local base=block(hl.get_monitor(F)) local function free(b) if b then "#,
    r#"for s=1,10 do if not ws(b+s) and not T[b+s] then return b+s end end return nil end local "#,
    r#"n=1 for _,x in ipairs(hl.get_workspaces()) do if x.id>=n then n=x.id+1 end end while "#,
    r#"T[n] do n=n+1 end return n end local function aside(x) local n=free(block(x.monitor)) or "#,
    r#"free(nil) ren(x.id,n) end local function reg(x) return x and not x.special and x.id>0 "#,
    r#"end local function off(x) local m=x and x.monitor return m and m.name~=F and not "#,
    r#"m.name:match("^XR%-") end D(hl.dsp.focus,{monitor=F}) local cnt={} for _,e in ipairs(W) "#,
    r#"do local t=T[e[2]] local w=win(e[1]) local c=w and w.workspace if t and t[2]==1 and "#,
    r#"reg(c) and not off(c) and (c.id==e[2] or not T[c.id]) then cnt[e[2]]=cnt[e[2]] or {} "#,
    r#"cnt[e[2]][c.id]=(cnt[e[2]][c.id] or 0)+1 end end local L={} for t,m in pairs(cnt) do for "#,
    r#"c,k in pairs(m) do L[#L+1]={k,t,c} end end table.sort(L,function(a,b) if a[1]~=b[1] then "#,
    r#"return a[1]>b[1] end if a[2]~=b[2] then return a[2]<b[2] end return a[3]<b[3] end) local "#,
    r#"got,used,tmp={},{},{} for _,x in ipairs(L) do if not got[x[2]] and not used[x[3]] then "#,
    r#"got[x[2]]=x[3] used[x[3]]=true end end for _,s in ipairs(S) do local c=got[s[1]] if c "#,
    r#"and c~=s[1] then local n=free(nil) ren(c,n) if ws(n) then tmp[#tmp+1]={n,s[1]} end end "#,
    r#"end for _,p in ipairs(tmp) do local x=ws(p[2]) if x then aside(x) end if not ws(p[2]) "#,
    r#"then ren(p[1],p[2]) end end for _,e in ipairs(W) do local w=win(e[1]) if w and not "#,
    r#"w.pinned and w.workspace and w.workspace.id~=e[2] and not off(w.workspace) then "#,
    r#"D(hl.dsp.window.move,{workspace=tostring(e[2]),follow=false,window="address:"..e[1]}) "#,
    r#"end end for _,s in ipairs(S) do local x=ws(s[1]) if x and x.monitor and "#,
    r#"x.monitor.name~=F and not off(x) then "#,
    r#"D(hl.dsp.workspace.move,{workspace=tostring(s[1]),monitor=F}) end end local X={} for _,x "#,
    r#"in ipairs(hl.get_workspaces()) do if reg(x) and not T[x.id] and (x.windows or 0)>0 and "#,
    r#"x.monitor and x.monitor.name:match("^XR%-%d+$") then X[#X+1]=x.id end end table.sort(X) "#,
    r#"for _,id in ipairs(X) do if base and not (id>base and id<=base+10) then local "#,
    r#"want=base+(id-1)%10+1 local n=(not ws(want) and not T[want]) and want or free(base) if n "#,
    r#"then ren(id,n) if ws(n) then id=n end end end "#,
    r#"D(hl.dsp.workspace.move,{workspace=tostring(id),monitor=F}) end local mine={} for _,e in "#,
    r#"ipairs(W) do local w=win(e[1]) if w and w.workspace and w.workspace.id==e[2] and not "#,
    r#"off(w.workspace) then mine[#mine+1]=e local sel="address:"..e[1] if (w.fullscreen or "#,
    r#"0)~=0 or (w.fullscreen_client or 0)~=0 then "#,
    r#"D(hl.dsp.window.fullscreen_state,{internal=0,client=0,action="set",window=sel}) end if "#,
    r#"w.floating~=(e[3]==1) then D(hl.dsp.window.float,{action=e[3]==1 and "on" or "#,
    r#""off",window=sel}) end end end local function tl(w) return w and not w.floating and not "#,
    r#"w.group and (w.fullscreen or 0)==0 end local by={} for _,e in ipairs(mine) do if e[3]==0 "#,
    r#"and e[8]==0 and tl(win(e[1])) then by[e[2]]=by[e[2]] or {} local g=by[e[2]] "#,
    r#"g[#g+1]={a=e[1],x=e[4],y=e[5]} end end local function yx(p,q) if p.y~=q.y then return "#,
    r#"p.y<q.y end if p.x~=q.x then return p.x<q.x end return p.a<q.a end for t,g in pairs(by) "#,
    r#"do local cur,inq={},{} for _,p in ipairs(g) do inq[p.a]=true end for _,w in "#,
    r#"ipairs(hl.get_windows()) do if tl(w) and w.workspace and w.workspace.id==t then "#,
    r#"cur[#cur+1]={a=w.address,x=w.at.x,y=w.at.y} end end table.sort(g,yx) table.sort(cur,yx) "#,
    r#"for _,p in ipairs(cur) do if not inq[p.a] then g[#g+1]=p end end local pos={} for i,p in "#,
    r#"ipairs(cur) do pos[p.a]=i end for k,p in ipairs(g) do local b=cur[k] and cur[k].a if b "#,
    r#"and pos[p.a] and b~=p.a then "#,
    r#"D(hl.dsp.window.swap,{window="address:"..p.a,target="address:"..b}) local i=pos[p.a] "#,
    r#"cur[i],cur[k]=cur[k],cur[i] pos[b],pos[p.a]=i,k end end end for _,e in ipairs(mine) do "#,
    r#"local sel="address:"..e[1] if e[3]==1 then if e[6]>0 and e[7]>0 then "#,
    r#"D(hl.dsp.window.resize,{x=e[6],y=e[7],window=sel}) end "#,
    r#"D(hl.dsp.window.move,{x=e[4],y=e[5],window=sel}) end if e[8]~=0 or e[9]~=0 then "#,
    r#"D(hl.dsp.window.fullscreen_state,{internal=e[8],client=e[9],action="set",window=sel}) "#,
    r#"end end if A>0 and not (off(ws(A)) and (ws(A).windows or 0)>0) then local x=ws(A) if x "#,
    r#"and x.monitor and x.monitor.name~=F then if (x.windows or 0)==0 then aside(x) else "#,
    r#"D(hl.dsp.workspace.move,{workspace=tostring(A),monitor=F}) end x=ws(A) end "#,
    r#"D(hl.dsp.focus,{monitor=F}) if x then pcall(function() "#,
    r#"hl.get_monitor(F):set_workspace({workspace=x.name}) end) else "#,
    r#"D(hl.dsp.focus,{workspace=tostring(A)}) end end if P~="" and win(P) then "#,
    r#"D(hl.dsp.focus,{window="address:"..P}) elseif PM~="" and hl.get_monitor(PM) then "#,
    r#"D(hl.dsp.focus,{monitor=PM}) end"#,
);

pub const UNPIN_LUA: &str = concat!(
    r#"for _,w in ipairs(hl.get_windows()) do local m=w.pinned and w.workspace and "#,
    r#"w.workspace.monitor if m and m.name:match("^XR%-%d+$") then pcall(function() "#,
    r#"hl.dispatch(hl.dsp.window.pin({action="disable",window="address:"..w.address})) end) end "#,
    r#"end"#,
);

/// The restore eval for a snapshot: one, at most EVAL_LIMIT.
pub fn lua(s: &Snapshot) -> Result<String> {
    validate(s)?;
    let spaces: Vec<String> = s
        .workspaces
        .iter()
        .map(|&(id, whole)| format!("{{{id},{}}}", u8::from(whole)))
        .collect();
    let windows: Vec<String> = s
        .windows
        .iter()
        .map(|w| {
            format!(
                "{{\"{}\",{},{},{},{},{},{},{},{}}}",
                w.address,
                w.workspace,
                u8::from(w.floating),
                w.at.0,
                w.at.1,
                w.size.0,
                w.size.1,
                w.fullscreen,
                w.client
            )
        })
        .collect();
    let code = format!(
        "local F=\"{}\" local A={} local P=\"{}\" local PM=\"{}\" local S={{{}}} local W={{{}}} {LUA}",
        s.monitor,
        s.active.unwrap_or(0),
        s.focus.as_deref().unwrap_or(""),
        s.focus_monitor.as_deref().unwrap_or(""),
        spaces.join(","),
        windows.join(",")
    );
    if code.len() > EVAL_LIMIT {
        return Err(Refused("layout too large for one restore"));
    }
    Ok(code)
}
