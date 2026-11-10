//! window_memory.py: a remembered window put back where the last session had it, in evals under the limit.

use super::{EVAL_LIMIT, Refused, Result, address, adopt, output, token};

pub const COORD: i64 = 1 << 20;
/// Windows remembered per glasses model.
pub const MAX_WINDOWS: i64 = 256;

/// A placement plan (window_memory.plan's result, `placed` left out: the evals never read it).
#[derive(Clone, Debug, Default)]
pub struct Placement {
    /// `(workspace id, output, slot id)`: workspaces that move whole.
    pub whole: Vec<(i64, String, i64)>,
    /// `(address, output, workspace id wanted, moved 0|1)`.
    pub windows: Vec<(String, String, i64, i64)>,
    /// `(address, layout)`.
    pub layouts: Vec<(String, String)>,
    /// `(address, floating 0|1, x, y, w, h)`.
    pub state: Vec<(String, i64, i64, i64, i64, i64)>,
    /// Per workspace, tiled windows in order: `(address, column, w, h)`.
    pub order: Vec<Vec<(String, i64, i64, i64)>>,
    /// `(address, internal, client)`.
    pub fullscreen: Vec<(String, i64, i64)>,
    /// `(output, address)`: the workspace each desktop shows.
    pub shown: Vec<(String, String)>,
    pub focus: Option<String>,
    pub monitor: Option<String>,
}

const LUA_PICK: &str = concat!(
    r#"local G={} local function pick(m,id,own) local b=ow and "#,
    r#"type(ow.workspace_id)=="function" and ow.workspace_id(m,1)-1 or nil if b and not (id>b "#,
    r#"and id<=b+10) then id=b+(id-1)%10+1 end local x=hl.get_workspace(id) if x and "#,
    r#"((x.monitor and x.monitor.name~=m.name) or (own and (x.windows or 0)>0)) then id=b and "#,
    r#"free(b) or nil end return id end "#,
);

const LUA_WHOLE: &str = concat!(
    r#"for _,e in ipairs(W) do pcall(function() "#,
    r#"hl.dispatch(hl.dsp.workspace.move({workspace=tostring(e[1]),monitor=e[2]})) "#,
    r#"home(e[1],e[2],e[3]) end) end "#,
);

const LUA_LOOSE: &str = concat!(
    r#"for _,e in ipairs(C) do pcall(function() local m=hl.get_monitor(e[2]) if not m then "#,
    r#"return end local k=e[2]..":"..e[3] local id=G[k] or pick(m,e[3],e[4]==1) or "#,
    r#"(m.active_workspace and m.active_workspace.id) if not id or id<=0 then return end "#,
    r#"G[k]=id "#,
    r#"hl.dispatch(hl.dsp.window.move({workspace=tostring(id),follow=false,window="address:"..e[1]})) "#,
    r#"local x=hl.get_workspace(id) if x and x.monitor and x.monitor.name~=e[2] then "#,
    r#"hl.dispatch(hl.dsp.workspace.move({workspace=tostring(id),monitor=e[2]})) end end) end"#,
);

const LUA_D: &str = concat!(
    r#"local function D(f,a) pcall(function() hl.dispatch(f(a)) end) end local function win(a) "#,
    r#"return hl.get_window("address:"..a) end "#,
);

const LUA_CURSOR: &str = r#"if p then D(hl.dsp.cursor.move,{x=p.x,y=p.y}) end"#;

const LUA_STATE: &str = concat!(
    r#"for _,e in ipairs(L) do local w=win(e[1]) local x=w and w.workspace if x and "#,
    r#"x.tiled_layout and x.tiled_layout~=e[2] then "#,
    r#"pcall(hl.workspace_rule,{workspace=tostring(x.id),layout=e[2]}) end end for _,e in "#,
    r#"ipairs(S) do local w=win(e[1]) if w then local s="address:"..e[1] if (w.fullscreen or "#,
    r#"0)~=0 or (w.fullscreen_client or 0)~=0 then "#,
    r#"D(hl.dsp.window.fullscreen_state,{internal=0,client=0,action="set",window=s}) end if "#,
    r#"w.floating~=(e[2]==1) then D(hl.dsp.window.float,{action=e[2]==1 and "on" or "#,
    r#""off",window=s}) end if e[2]==1 then if e[5]>0 and e[6]>0 then "#,
    r#"D(hl.dsp.window.resize,{x=e[5],y=e[6],window=s}) end "#,
    r#"D(hl.dsp.window.move,{x=e[3],y=e[4],window=s}) end end end"#,
);

const LUA_ORDER: &str = concat!(
    r#"local p=hl.get_cursor_pos() local function tl(w) return w and not w.floating and not "#,
    r#"w.group and (w.fullscreen or 0)==0 end local function xy(a,b) if a.x~=b.x then return "#,
    r#"a.x<b.x end if a.y~=b.y then return a.y<b.y end return a.a<b.a end local id for _,r in "#,
    r#"ipairs(G) do local w=win(r[1]) if w and w.workspace then id=w.workspace.id break end end "#,
    r#"local R={} for _,r in ipairs(G) do local w=win(r[1]) if tl(w) and w.workspace.id==id "#,
    r#"then R[#R+1]=r end end local function slots() local cur,inq,g={}, {}, {} for _,r in "#,
    r#"ipairs(R) do inq[r[1]]=true g[#g+1]=r[1] end for _,w in ipairs(hl.get_windows()) do if "#,
    r#"tl(w) and w.workspace and w.workspace.id==id then "#,
    r#"cur[#cur+1]={a=w.address,x=w.at.x,y=w.at.y} end end table.sort(cur,xy) for _,c in "#,
    r#"ipairs(cur) do if not inq[c.a] then g[#g+1]=c.a end end local pos={} for i,c in "#,
    r#"ipairs(cur) do pos[c.a]=i end for k,a in ipairs(g) do local b=cur[k] and cur[k].a if b "#,
    r#"and pos[a] and b~=a then "#,
    r#"D(hl.dsp.window.swap,{window="address:"..a,target="address:"..b}) local i=pos[a] "#,
    r#"cur[i],cur[k]=cur[k],cur[i] pos[b],pos[a]=i,k end end end local function col(a) local "#,
    r#"w=win(a) return w and w.layout and w.layout.column end local function fo(a) "#,
    r#"D(hl.dsp.focus,{window="address:"..a}) end local function M(m) D(hl.dsp.layout,m) end if "#,
    r#"id then slots() local ok,d=pcall(hl.get_config,"scrolling.direction") local h=not (ok "#,
    r#"and (d=="left" or d=="up" or d=="down")) if R[1] and col(R[1][1]) then if h then local "#,
    r#"bad=false for i=2,#R do local a,b=col(R[i-1][1]),col(R[i][1]) if "#,
    r#"(R[i][2]==R[i-1][2])~=((a and b and a.index==b.index) or false) then bad=true end end if "#,
    r#"bad then for _,r in ipairs(R) do local c=col(r[1]) local n=c and #c.windows or 1 if n>1 "#,
    r#"then fo(r[1]) for _=2,n do M("expel") end end end slots() local i=1 while i<=#R do local "#,
    r#"j=i while R[j+1] and R[j+1][2]==R[i][2] do j=j+1 end if j>i then fo(R[i][1]) for _=i+1,j "#,
    r#"do M("consume") end end i=j+1 end end local last for _,r in ipairs(R) do if r[2]~=last "#,
    r#"then last=r[2] local w,c=win(r[1]),col(r[1]) if w and c and r[3]>0 and w.size.x>0 and "#,
    r#"math.abs(w.size.x-r[3])>8 then fo(r[1]) M(string.format("colresize "#,
    r#"%.4f",math.max(0.05,math.min(1,c.width*r[3]/w.size.x)))) end end end end else for _,r in "#,
    r#"ipairs(R) do local w=win(r[1]) if w and r[3]>0 and r[4]>0 and (math.abs(w.size.x-r[3])>2 "#,
    r#"or math.abs(w.size.y-r[4])>2) then "#,
    r#"D(hl.dsp.window.resize,{x=r[3],y=r[4],window="address:"..r[1]}) end end end end "#,
);

const LUA_END: &str = concat!(
    r#"local p=hl.get_cursor_pos() for _,e in ipairs(F) do "#,
    r#"D(hl.dsp.window.fullscreen_state,{internal=e[2],client=e[3],action="set",window="address:"..e[1]}) "#,
    r#"end for _,e in ipairs(A) do local w=win(e[2]) local x=w and w.workspace local "#,
    r#"m=hl.get_monitor(e[1]) if x and m and x.monitor and x.monitor.name==e[1] then "#,
    r#"pcall(function() m:set_workspace({workspace=x.name}) end) end end if P~="" and win(P) "#,
    r#"then D(hl.dsp.focus,{window="address:"..P}) elseif PM~="" and hl.get_monitor(PM) then "#,
    r#"D(hl.dsp.focus,{monitor=PM}) end "#,
);

fn within(v: i64, low: i64, high: i64) -> bool {
    (low..=high).contains(&v)
}

fn layout(s: &str) -> bool {
    (1..=32).contains(&s.len()) && s.bytes().all(|b| b.is_ascii_lowercase())
}

fn validated(p: &Placement) -> Result<&Placement> {
    const ID: i64 = 2147483647;
    let ok = p
        .whole
        .iter()
        .all(|(id, o, want)| within(*id, 1, ID) && output(o) && within(*want, 1, ID))
        && p.windows.iter().all(|(a, o, want, moved)| {
            address(a) && output(o) && within(*want, 1, ID) && within(*moved, 0, 1)
        })
        && p.layouts.iter().all(|(a, l)| address(a) && layout(l))
        && p.state.iter().all(|(a, f, x, y, w, h)| {
            address(a)
                && within(*f, 0, 1)
                && within(*x, -COORD, COORD)
                && within(*y, -COORD, COORD)
                && within(*w, 0, COORD)
                && within(*h, 0, COORD)
        })
        && p.order.iter().flatten().all(|(a, c, w, h)| {
            address(a) && within(*c, 0, MAX_WINDOWS) && within(*w, 0, COORD) && within(*h, 0, COORD)
        })
        && p.fullscreen
            .iter()
            .all(|(a, i, c)| address(a) && within(*i, 0, 3) && within(*c, 0, 3))
        && p.shown.iter().all(|(o, a)| output(o) && address(a))
        && p.focus.as_deref().is_none_or(address)
        && p.monitor.as_deref().is_none_or(token);
    if ok {
        Ok(p)
    } else {
        Err(Refused("invalid window placement"))
    }
}

/// One Lua row: strings quoted, numbers bare (window_memory._rows).
enum Cell<'a> {
    S(&'a str),
    I(i64),
}

fn row(cells: &[Cell]) -> String {
    let cells: Vec<String> = cells
        .iter()
        .map(|c| match c {
            Cell::S(s) => format!("\"{s}\""),
            Cell::I(i) => i.to_string(),
        })
        .collect();
    format!("{{{}}}", cells.join(","))
}

/// A rendered row and the table it goes into.
#[derive(Clone)]
struct Row {
    kind: char,
    text: String,
}

fn rows(rows: &[Row], kind: char) -> String {
    rows.iter()
        .filter(|r| r.kind == kind)
        .map(|r| r.text.as_str())
        .collect::<Vec<_>>()
        .join(",")
}

/// Evals for `groups` (rows that belong together), each under EVAL_LIMIT: a group goes whole into an eval while it
/// fits one, and only one larger than that is cut.
fn batches(groups: Vec<Vec<Row>>, make: impl Fn(&[Row]) -> String) -> Vec<String> {
    let fits = |rows: &[Row]| make(rows).len() < EVAL_LIMIT;
    let (mut out, mut part) = (Vec::new(), Vec::<Row>::new());
    for group in groups {
        if !part.is_empty() && !fits(&[part.as_slice(), group.as_slice()].concat()) {
            out.push(make(&part));
            part.clear();
        }
        for r in group {
            if !part.is_empty() && !fits(&[part.as_slice(), std::slice::from_ref(&r)].concat()) {
                out.push(make(&part));
                part.clear();
            }
            part.push(r);
        }
    }
    if !part.is_empty() {
        out.push(make(&part));
    }
    out
}

fn moves_eval(r: &[Row]) -> String {
    format!(
        "local C={{{}}} local W={{{}}} {}{LUA_PICK}{LUA_WHOLE}{LUA_LOOSE}",
        rows(r, 'C'),
        rows(r, 'W'),
        adopt::LUA_HEAD
    )
}

/// The evals that put the windows on their workspaces: workspaces first, so loose windows join them. Empty when
/// nothing moves.
pub fn moves_lua(placement: &Placement) -> Result<Vec<String>> {
    let p = validated(placement)?;
    let mut groups: Vec<Vec<Row>> = p
        .whole
        .iter()
        .map(|(id, o, want)| {
            vec![Row {
                kind: 'W',
                text: row(&[Cell::I(*id), Cell::S(o), Cell::I(*want)]),
            }]
        })
        .collect();
    let mut index: Vec<((&str, i64), usize)> = Vec::new();
    for (a, o, want, moved) in &p.windows {
        let key = (o.as_str(), *want);
        let at = match index.iter().find(|(k, _)| *k == key) {
            Some(&(_, at)) => at,
            None => {
                index.push((key, groups.len()));
                groups.push(Vec::new());
                groups.len() - 1
            }
        };
        groups[at].push(Row {
            kind: 'C',
            text: row(&[Cell::S(a), Cell::S(o), Cell::I(*want), Cell::I(*moved)]),
        });
    }
    Ok(batches(groups, moves_eval))
}

fn state_eval(r: &[Row]) -> String {
    format!(
        "local L={{{}}} local S={{{}}} {LUA_D}{LUA_STATE}",
        rows(r, 'L'),
        rows(r, 'S')
    )
}

fn order_eval(r: &[String]) -> String {
    format!("local G={{{}}} {LUA_D}{LUA_ORDER}{LUA_CURSOR}", r.join(","))
}

fn end_eval(r: &[Row], p: &Placement) -> String {
    let last = r.iter().any(|r| r.kind == 'E');
    let shown = if last {
        p.shown
            .iter()
            .map(|(o, a)| row(&[Cell::S(o), Cell::S(a)]))
            .collect::<Vec<_>>()
            .join(",")
    } else {
        String::new()
    };
    let (focus, monitor) = if last {
        (
            p.focus.as_deref().unwrap_or(""),
            p.monitor.as_deref().unwrap_or(""),
        )
    } else {
        ("", "")
    };
    format!(
        "local F={{{}}} local A={{{shown}}} local P=\"{focus}\" local PM=\"{monitor}\" {LUA_D}{LUA_END}{LUA_CURSOR}",
        rows(r, 'F')
    )
}

/// The evals after the moves: state, then one per workspace's tile order, then fullscreen, what each desktop shows
/// and focus. A workspace with more tiled windows than one eval holds gets the first ones in order.
pub fn settle_lua(placement: &Placement) -> Result<Vec<String>> {
    let p = validated(placement)?;
    let mut out = Vec::new();
    if !p.state.is_empty() || !p.layouts.is_empty() {
        let layouts = p.layouts.iter().map(|(a, l)| {
            vec![Row {
                kind: 'L',
                text: row(&[Cell::S(a), Cell::S(l)]),
            }]
        });
        let state = p.state.iter().map(|(a, f, x, y, w, h)| {
            let cells = [
                Cell::S(a),
                Cell::I(*f),
                Cell::I(*x),
                Cell::I(*y),
                Cell::I(*w),
                Cell::I(*h),
            ];
            vec![Row {
                kind: 'S',
                text: row(&cells),
            }]
        });
        out = batches(layouts.chain(state).collect(), state_eval);
    }
    for order in &p.order {
        let mut r: Vec<String> = order
            .iter()
            .map(|(a, c, w, h)| row(&[Cell::S(a), Cell::I(*c), Cell::I(*w), Cell::I(*h)]))
            .collect();
        while !r.is_empty() && order_eval(&r).len() >= EVAL_LIMIT {
            r.pop();
        }
        if !r.is_empty() {
            out.push(order_eval(&r));
        }
    }
    let mut tail: Vec<Vec<Row>> = p
        .fullscreen
        .iter()
        .map(|(a, i, c)| {
            vec![Row {
                kind: 'F',
                text: row(&[Cell::S(a), Cell::I(*i), Cell::I(*c)]),
            }]
        })
        .collect();
    if !p.shown.is_empty() || p.focus.is_some() || p.monitor.is_some() {
        tail.push(vec![Row {
            kind: 'E',
            text: String::new(),
        }]);
    }
    if !tail.is_empty() {
        out.extend(batches(tail, |r| end_eval(r, p)));
    }
    Ok(out)
}

/// For bind_check: every generated eval on a sample plan, then each dispatcher they call with the arguments they give,
/// so Hyprland's own loader and argument checks see them.
pub fn verify_lua() -> Vec<String> {
    let (a, b) = ("0x1", "0x2");
    let s = |v: &str| v.to_string();
    let sample = Placement {
        whole: vec![(1, s("XR-1"), 61)],
        windows: vec![(s(a), s("XR-2"), 71, 0), (s(b), s("XR-3"), 81, 1)],
        layouts: vec![(s(a), s("scrolling"))],
        state: vec![(s(a), 1, 10, 20, 300, 400), (s(b), 0, 0, 0, 0, 0)],
        order: vec![vec![(s(a), 0, 300, 400), (s(b), 1, 300, 0)]],
        fullscreen: vec![(s(a), 2, 2)],
        shown: vec![(s("XR-1"), s(a))],
        focus: Some(s(a)),
        monitor: Some(s("DP-1")),
    };
    let w = format!("window=\"address:{a}\"");
    let calls = [
        s("hl.dsp.layout(\"consume\")"),
        s("hl.dsp.layout(\"expel\")"),
        s("hl.dsp.layout(\"colresize 0.5000\")"),
        format!("hl.dsp.window.swap({{{w},target=\"address:{b}\"}})"),
        format!("hl.dsp.window.float({{action=\"on\",{w}}})"),
        format!("hl.dsp.window.resize({{x=300,y=400,{w}}})"),
        format!("hl.dsp.window.move({{x=10,y=20,{w}}})"),
        format!("hl.dsp.window.move({{workspace=\"61\",follow=false,{w}}})"),
        format!("hl.dsp.window.fullscreen_state({{internal=2,client=2,action=\"set\",{w}}})"),
        s("hl.dsp.workspace.move({workspace=\"61\",monitor=\"XR-1\"})"),
        s("hl.dsp.workspace.change_id({workspace=\"61\",id=62})"),
        format!("hl.dsp.focus({{{w}}})"),
        s("hl.dsp.focus({monitor=\"XR-1\"})"),
        s("hl.dsp.cursor.move({x=1,y=2})"),
    ];
    let mut out = moves_lua(&sample).expect("sample plan is valid");
    out.extend(settle_lua(&sample).expect("sample plan is valid"));
    out.push(
        calls
            .iter()
            .map(|c| format!("assert({c})"))
            .collect::<Vec<_>>()
            .join(" "),
    );
    out
}
