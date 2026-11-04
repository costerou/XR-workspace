import QtQuick
import QtTest
import "../Model.js" as Model
import ".." as Workspace

TestCase {
    id: testCase
    name: "WorkspaceModel"
    when: windowShown
    visible:true
    width:700; height:860
    Component { id:confirmationComponent; Workspace.ConfirmDialog {} }
    Component { id:desktopComponent; Workspace.DesktopEditor {} }
    Component { id:placementComponent; Workspace.PlacementEditor {} }
    SignalSpy { id:placementSpy; signalName:"save" }
    SignalSpy { id:confirmationSpy; signalName:"confirm" }
    function test_confirmationControls() {
        const dialog=createTemporaryObject(confirmationComponent,testCase,{width:640,pending:{action:"start",scope_id:"test",scope:{model:"luma-ultra",output:"DP-2",recovery_output:"DP-1",desktops:[{width:1920,height:1080,scale:1}],tracking:"xrlinuxdriver",mode_switch:{width:1920,height:1200,refresh_hz:120}}}});
        verify(dialog !== null);
        verify(waitForRendering(dialog));
        const tracking=findChild(dialog,"tracking");
        const mode=findChild(dialog,"mode_switch");
        verify(tracking.enabled && tracking.checked);
        verify(mode.enabled && mode.checked);
        verify(!findChild(dialog,"requiredScope").enabled);
        confirmationSpy.target=dialog;
        confirmationSpy.clear();
        mouseClick(tracking);
        mouseClick(mode);
        mouseClick(findChild(dialog,"confirmScope"));
        compare(confirmationSpy.count,1);
        compare(confirmationSpy.signalArguments[0][0],{tracking:false,mode_switch:false});
        dialog.busy=true;
        verify(!tracking.enabled);
        verify(!findChild(dialog,"confirmScope").enabled);
        confirmationSpy.target=null;
    }
    function test_desktopControls() {
        const editor=createTemporaryObject(desktopComponent,testCase,{width:660,desktop:{width:3840,height:1080,scale:1}});
        verify(editor !== null);
        verify(waitForRendering(editor));
        verify(!findChild(editor,"scale1.75").enabled);
        mouseClick(findChild(editor,"scale1.25"));
        compare(editor.draftScale,1.25);
        verify(findChild(editor,"scale2.5").enabled);  // 250% and 300% too (the pill's Scale dropdown's steps)
        mouseClick(findChild(editor,"scale3"));
        compare(editor.draftScale,3);
        editor.desktop={width:1920,height:1080,scale:2};
        compare(editor.draftWidth,1920);
        compare(editor.draftScale,2);
    }
    function test_scales() {
        compare(Model.validScales(1920, 1080), [1, 1.25, 1.5, 2, 2.5, 3])
        compare(Model.validScales(2560, 1440), [1, 1.25, 2, 2.5])
        compare(Model.SCALES, [1, 1.25, 1.5, 1.75, 2, 2.5, 3])
        compare(Model.validScales(3440, 1440), [1, 1.25, 2, 2.5])
        compare(Model.validScales(5120, 1440), [1, 1.25, 2, 2.5])
        compare(Model.validScales(3840, 1080), [1, 1.25, 1.5, 2, 2.5, 3])
        compare(Model.validScales(0, 1080), [])
        compare(Model.validScales(true, 1080), [])
    }
    function test_presets() {
        compare(Model.presetsFor("16:9"), [{width:1920,height:1080}, {width:2560,height:1440}])
        compare(Model.presetsFor("21:9"), [{width:2560,height:1080}, {width:3440,height:1440}])
        compare(Model.presetsFor("32:9"), [{width:3840,height:1080}, {width:5120,height:1440}])
        compare(Model.presetsFor("custom"), [])
        let copy = Model.presetsFor("16:9")
        copy[0].width = 1
        compare(Model.presetsFor("16:9")[0].width, 1920)
    }
    function test_shapes() {
        compare(Model.shapeOf(3440, 1440), "21:9")
        compare(Model.shapeOf(2560, 1080), "21:9")
        compare(Model.shapeOf(3840, 1080), "32:9")
        compare(Model.shapeOf(2560, 1440), "16:9")
        compare(Model.shapeOf(1000, 800), "custom")
    }
    function test_preview() {
        const desktops = [{width:1920,height:1080}, {width:3840,height:1080}, {width:3440,height:1440}]
        const rects = Model.previewRects(desktops, 800, 400, 20)
        compare(rects.length, 3)
        for (let i = 0; i < 3; ++i) {
            fuzzyCompare(rects[i].height, rects[0].height, 0.001)
            fuzzyCompare(rects[i].width / rects[i].height, desktops[i].width / desktops[i].height, 0.001)
            verify(rects[i].x >= 0 && rects[i].y >= 0)
            verify(rects[i].x + rects[i].width <= 800.001)
            if (i) fuzzyCompare(rects[i].x - rects[i-1].x - rects[i-1].width, 20, 0.001)
        }
        const small = Model.previewRects([{width:1920,height:1080}], 800, 100, 20)[0]
        compare(small.height, 100)
        fuzzyCompare(small.x + small.width / 2, 400, 0.001)
        compare(Model.previewRects([], 800, 400, 20), [])
        compare(Model.previewRects(desktops, 10, 400, 20), [])
    }
    function test_colors() {
        compare(Model.barColorRole("running"), "accent")
        for (const state of ["recovery-needed", "cli-recovery", "failed"])
            compare(Model.barColorRole(state), "warning")
        for (const state of ["idle", "no-glasses", "confirming", "starting", "stopping", "unknown"])
            compare(Model.barColorRole(state), "muted")
    }
    function test_warning() {
        verify(Model.largeLayoutWarning([{width:1920,height:1080}, {width:5120,height:1440}, {width:1920,height:1080}]))
        verify(!Model.largeLayoutWarning(Array(3).fill({width:1920,height:1080})))
    }
    function test_scopeLines() {
        const scope = {model:"luma-ultra", output:"DP-2", recovery_output:"DP-1",
            desktops:[{width:3840,height:1080,scale:1.25}], tracking:"xrlinuxdriver",
            mode_switch:{width:1920,height:1200,refresh_hz:120}};
        const lines = Model.scopeLines(scope, "start");
        compare(lines.filter(l => l.optional).map(l => l.key), ["tracking", "mode_switch"]);
        verify(lines.some(l => l.key === "tracking" && l.text === "Head tracking via XRLinuxDriver, network blocked."));
        // The Luma scope says it can hand over: the click agrees to both drivers.
        const luma = Model.scopeLines(Object.assign({}, scope, {tracking:"luma"}), "start").filter(l => l.key === "tracking");
        compare(luma.length, 1);
        verify(luma[0].optional && /XR Luma driver/.test(luma[0].text) && /XRLinuxDriver takes over/.test(luma[0].text));
        verify(lines.some(l => l.text.indexOf("3840 × 1080 · 125%") >= 0));
        verify(lines.some(l => l.text.indexOf("DP-1") >= 0));
        verify(Model.scopeLines(scope, "recover").every(l => !l.optional));
        // A line Recover takes out of monitors.lua is part of what the click agrees to; none, no line about it.
        const rule = Model.scopeLines(scope, "recover", [{name:"XR-1", line:9, removable:true}]);
        verify(rule.some(l => l.text.indexOf("XR-1") >= 0 && l.text.indexOf("monitors.lua (line 9)") >= 0 && !l.optional));
        verify(!Model.scopeLines(scope, "recover", []).some(l => l.text.indexOf("monitors.lua") >= 0));
        // The pills can change the desktops during the session: the start scope says so, the recover scope does not
        // list the start's desktops (the run may have others by then).
        verify(lines.some(l => !l.optional && l.text.indexOf("ultrawide") >= 0 && l.text.indexOf("up to " + Model.MAX_DESKTOPS) >= 0));
        verify(Model.scopeLines(scope, "recover").some(l => l.text.indexOf("XR-" + Model.MAX_DESKTOPS) >= 0));
        compare(Model.scopeLines(Object.assign({}, scope, {model:"beast", mode_switch:false}), "start").filter(l => l.key === "mode_switch").length, 0);
    }
    function test_desktopEdit() {
        compare(Model.desktopEdit(3840,1080,1.75), null);
        compare(Model.desktopEdit(641,1080,1), null);
        compare(Model.desktopEdit(7682,1080,1), null);
        compare(Model.desktopEdit(3440,1440,1.25), {width:3440,height:1440,scale:1.25});
        compare(Model.desktopEdit(1920,1080,3), {width:1920,height:1080,scale:3});
        compare(Model.desktopEdit(1920,1080,2.25), null);
    }
    // Maximize-follow (2026-11-03): the panel says Maximized / Overview, never "zoom"; the overview is always anchored.
    function test_incidentLine() {
        compare(Model.incidentLine({}), "");
        compare(Model.incidentLine(undefined), "");
        compare(Model.incidentLine({incident:null}), "");
        const at = new Date(2026, 10, 3, 20, 6, 12);
        compare(Model.incidentLine({incident:{summary:"the glasses' USB connection dropped and the session ended", at:at.toISOString()}}),
                "Last problem (Nov 3, 20:06): the glasses' USB connection dropped and the session ended.");
        compare(Model.incidentLine({incident:{summary:"the XR renderer crashed", at:"not a time"}}), "Last problem: the XR renderer crashed.");
        compare(Model.incidentLine({incident:{summary:"the XR renderer crashed"}}), "Last problem: the XR renderer crashed.");
        compare(Model.incidentLine({incident:{kind:"usb-drop"}}), "");
    }

    function test_viewLabels() {
        compare(Model.viewLabel({}), "awaiting measured state");
        compare(Model.viewLabel(undefined), "awaiting measured state");
        compare(Model.viewLabel({zoomed:false, mode:"anchor"}), "Overview · anchor");
        compare(Model.viewLabel({zoomed:true, mode:"follow"}), "Maximized · follow");
        compare(Model.viewLabel({zoomed:true, mode:"locked"}), "Maximized · locked");
        compare(Model.operationLabel("zoom_in"), "Maximize");
        compare(Model.operationLabel("zoom_out"), "Overview");
        compare(Model.operationLabel("mode_next"), "Maximized: follow / locked");
        compare(Model.operationLabel("something_new"), "something_new");
        for (const op of ["prev","next","zoom_in","zoom_out","recenter","mode_next","spacing_up","spacing_down","prediction"])
            verify(Model.operationLabel(op).toLowerCase().indexOf("zoom") < 0, op);
    }
    function test_selectOperations() {
        compare(Model.selectOperations(0,3,["next","prev"]), ["next","next","next"]);  // a fourth desktop added in XR
        compare(Model.selectOperations(0,Model.MAX_DESKTOPS,["next"]), []);
        compare(Model.selectOperations(0,2,["next","prev"]), ["next","next"]);
        compare(Model.selectOperations(2,0,["next","prev"]), ["prev","prev"]);
        compare(Model.selectOperations(undefined,2,["next"]), []);
        compare(Model.selectOperations(0,2,[]), []);
        compare(Model.selectOperations(1,1,["next","prev"]), []);
    }
    function test_placementEdit() {
        const flat = Model.defaultPlacement();
        compare(Model.placementEdit(flat), flat);
        verify(Model.isFlat(flat));
        compare(flat, {x:0, y:0, distance:100, curvature:0});  // exactly the four fields of geometry.DEFAULT_PLACEMENT
        const edge = {x:-100, y:100, distance:200, curvature:120};
        compare(Model.placementEdit(edge), edge);
        // x / y are the orbit in tenths of a degree: right round to behind you, up to straight overhead.
        const behind = {x:1800, y:-900, distance:50, curvature:0};
        compare(Model.placementEdit(behind), behind);
        const otherWay = Object.assign({}, flat, {x:-1800, y:900});
        compare(Model.placementEdit(otherWay), otherWay);
        for (const bad of [{x:1801}, {x:-1801}, {y:901}, {y:-901}, {x:2.5}, {y:-0.5}])
            compare(Model.placementEdit(Object.assign({}, flat, bad)), null, JSON.stringify(bad));
        for (const key of ["x","y","distance","curvature"]) {
            const b = Model.placementBounds(key);
            compare(Model.placementEdit(Object.assign({}, flat, {[key]:b[0]-1})), null);
            compare(Model.placementEdit(Object.assign({}, flat, {[key]:b[1]+1})), null);
            compare(Model.placementEdit(Object.assign({}, flat, {[key]:0.5})), null);
        }
        compare(Model.placementEdit(Object.assign({}, flat, {z:0})), null);
        // The facing settings are gone (every desktop faces you): a placement still carrying them is refused, even at 0.
        compare(Model.placementEdit(Object.assign({}, flat, {yaw:0, pitch:0, roll:0})), null);
        compare(Model.placementEdit(Object.assign({}, edge, {yaw:-60, pitch:45, roll:-45})), null);
        for (const key of ["yaw", "pitch", "roll"]) {
            compare(Model.placementEdit(Object.assign({}, flat, {[key]:0})), null, key);
            compare(Model.placementBounds(key), [0, 0], key);  // not a field
        }
        compare(Model.placementEdit({x:0, y:0, distance:100}), null);
        compare(Model.placementEdit(null), null);
        compare(Model.placementBounds("x"), [-1800, 1800]);
        compare(Model.placementBounds("y"), [-900, 900]);
        compare(Model.placementBounds("distance"), [10, 200]);  // down to 10: ten times the size
        compare(Model.placementBounds("curvature"), [0, 120]);
        Model.placementBounds("x")[0] = 0;  // a copy: the table stays as it is
        compare(Model.placementBounds("x"), [-1800, 1800]);
    }
    function test_placementValueText() {
        compare(Model.placementValueText("x", 125), "12.5°");
        compare(Model.placementValueText("x", -125), "-12.5°");
        compare(Model.placementValueText("y", -30), "-3°");
        compare(Model.placementValueText("y", 30), "3°");
        compare(Model.placementValueText("y", -5), "-0.5°");
        compare(Model.placementValueText("x", 1800), "180°");
        compare(Model.placementValueText("x", -1800), "-180°");
        compare(Model.placementValueText("x", -1799), "-179.9°");
        compare(Model.placementValueText("y", 900), "90°");
        compare(Model.placementValueText("y", -900), "-90°");
        compare(Model.placementValueText("x", 0), "0°");
        compare(Model.placementValueText("x", -0), "0°");
        compare(Model.placementValueText("distance", 100), "100%");
        compare(Model.placementValueText("distance", 50), "50%");
        compare(Model.placementValueText("curvature", 120), "120°");
        compare(Model.placementValueText("curvature", -0), "0°");
        for (const key of ["yaw", "pitch", "roll"])  // removed: not placement fields
            compare(Model.placementValueText(key, 20), "", key);
        for (const bad of [12.5, -0.1, NaN, Infinity, -Infinity, "5", null, undefined, true, [5], {}])
            for (const key of ["x", "y", "distance", "curvature"])
                compare(Model.placementValueText(key, bad), "", key + " " + String(bad));
        compare(Model.placementValueText("z", 5), "");
        compare(Model.placementValueText("toString", 5), "");
        compare(Model.placementValueText(undefined, 5), "");
    }
    function test_withPlacementKeepsTheDesktopCount() {
        const tilt = {x:10, y:0, distance:100, curvature:30}, flat = Model.defaultPlacement();
        compare(Model.withPlacement([flat, flat], 1, tilt, 2), [flat, tilt]);              // two desktops left in XR
        compare(Model.withPlacement([flat, flat, flat, flat], 3, tilt, 4), [flat, flat, flat, tilt]);
        compare(Model.withPlacement([flat, flat, flat, flat], 3, tilt), [flat, flat, flat, tilt]);
        compare(Model.withPlacement([flat], 3, tilt, 1), [flat]);                            // index past the desktops
        compare(Model.desktopName(["XR-1", "XR-3"], 1), "XR-3");
        compare(Model.desktopName([], 1), "XR-2");
        compare(Model.desktopName(undefined, 0), "XR-1");
    }
    function test_withPlacement() {
        const tilt = {x:10, y:0, distance:100, curvature:30};
        compare(Model.withPlacement(undefined, 1, tilt), [Model.defaultPlacement(), tilt, Model.defaultPlacement()]);
        compare(Model.withPlacement([tilt, {bad:1}], 2, tilt), [tilt, Model.defaultPlacement(), tilt]);
        compare(Model.withPlacement([tilt, tilt, tilt], 0, {x:999}), [tilt, tilt, tilt]);
        compare(Model.withPlacement([tilt, tilt, tilt], 0, Object.assign({}, tilt, {x:1801})), [tilt, tilt, tilt]);
        const behind = Object.assign(Model.defaultPlacement(), {x:-1800, y:900});
        compare(Model.withPlacement([tilt, tilt, tilt], 1, behind), [tilt, behind, tilt]);
        compare(Model.placementSummary(Model.defaultPlacement()), "flat, in line");
        compare(Model.placementSummary(tilt), "moved 1° right · curved 30°");
        compare(Model.placementSummary({x:-125, y:30, distance:150, curvature:20}),
                "moved 12.5° left, 3° up · distance 150% · curved 20°");
        // A placement still carrying the removed facing settings is invalid: it is not summarised, and not kept.
        const legacy = Object.assign({}, tilt, {yaw:20, pitch:-5, roll:3});
        compare(Model.placementSummary(legacy), "flat, in line");
        compare(Model.withPlacement([legacy, tilt, tilt], 1, legacy), [Model.defaultPlacement(), tilt, tilt]);
        compare(Model.placementSummary(Object.assign(Model.defaultPlacement(), {x:-125, y:-30})), "moved 12.5° left, 3° down");
        compare(Model.placementSummary(Object.assign(Model.defaultPlacement(), {x:125, y:30})), "moved 12.5° right, 3° up");
        compare(Model.placementSummary(Object.assign(Model.defaultPlacement(), {y:5})), "moved 0.5° up");
        compare(Model.placementSummary(Object.assign(Model.defaultPlacement(), {x:-1, y:-900})), "moved 0.1° left, 90° down");
        compare(Model.placementSummary(Object.assign(Model.defaultPlacement(), {x:1800, distance:150})),
                "moved 180° right · distance 150%");
        compare(Model.placementSummary(Object.assign(Model.defaultPlacement(), {x:-0, y:-0})), "flat, in line");
        compare(Model.placementSummary(Object.assign(Model.defaultPlacement(), {x:1801})), "flat, in line");  // invalid
        compare(Model.placementSummary(Object.assign(Model.defaultPlacement(), {x:12.5})), "flat, in line");
    }
    function test_placedRects() {
        const rects = [{x:0, y:0, width:100, height:50}];
        compare(Model.placedRects(rects, []), [{x:0, y:0, width:100, height:50}]);
        compare(Model.placedRects(rects, undefined), [{x:0, y:0, width:100, height:50}]);
        verify(!("rotation" in Model.placedRects(rects, [])[0]), "every desktop faces you: a tile is never rotated");
        compare(Model.placedRects([], []), []);
        compare(Model.placedRects([], [Model.defaultPlacement()]), []);
        compare(Model.placedRects(Model.previewRects([], 800, 400, 20), undefined), []);
        const r = Model.placedRects(rects, [{x:50, y:20, distance:200, curvature:60}])[0];
        fuzzyCompare(r.width, 50, 0.001);   // half size at 200%; it faces you, so it is never foreshortened
        fuzzyCompare(r.height, 25, 0.001);
        fuzzyCompare(r.x + r.width / 2, 50 + 5 * 50 / 7, 0.001);  // 5° right; a desktop is about 7° tall
        fuzzyCompare(r.y + r.height / 2, 25 - 2 * 50 / 7, 0.001);  // 2° and +y is up
        verify(!("rotation" in r));
        // A placement still carrying the removed facing settings is invalid and draws flat.
        compare(Model.placedRects(rects, [{x:50, y:20, distance:200, yaw:60, pitch:0, roll:10, curvature:0}]),
                [{x:0, y:0, width:100, height:50}]);
        const down = Model.placedRects(rects, [Object.assign(Model.defaultPlacement(), {x:-50, y:-20})])[0];
        fuzzyCompare(down.x + down.width / 2, 50 - 5 * 50 / 7, 0.001);
        fuzzyCompare(down.y + down.height / 2, 25 + 2 * 50 / 7, 0.001);
        fuzzyCompare(down.width, 100, 0.001);  // the orbit turns the desktop round you: its apparent size stays
        fuzzyCompare(down.height, 50, 0.001);
        const huge = Model.placedRects(rects, [Object.assign(Model.defaultPlacement(), {distance:10})])[0];
        compare(huge, {x:-450, y:-225, width:1000, height:500});  // ten times the size about its centre (the preview clips it)
        compare(Model.placementEdit(Object.assign(Model.defaultPlacement(), {distance:10})).distance, 10);
        compare(Model.placementEdit(Object.assign(Model.defaultPlacement(), {distance:9})), null);

        // A row of three: a centre is kept inside the row, so a desktop beside, behind or above you shows at its edge.
        const row = [{x:10, y:20, width:100, height:50}, {x:120, y:20, width:100, height:50}, {x:230, y:20, width:100, height:50}];
        const flat = Model.defaultPlacement();
        const placed = Model.placedRects(row, [Object.assign({}, flat, {x:-1800}), Object.assign({}, flat, {y:900}),
                                               Object.assign({}, flat, {x:1800, y:-900})]);
        compare(placed.length, 3);
        fuzzyCompare(placed[0].x + placed[0].width / 2, 10, 0.001);   // the row's left edge
        fuzzyCompare(placed[0].y + placed[0].height / 2, 45, 0.001);
        fuzzyCompare(placed[1].x + placed[1].width / 2, 170, 0.001);
        fuzzyCompare(placed[1].y + placed[1].height / 2, 20, 0.001);  // the row's top
        fuzzyCompare(placed[2].x + placed[2].width / 2, 330, 0.001);  // the row's right edge
        fuzzyCompare(placed[2].y + placed[2].height / 2, 70, 0.001);  // the row's bottom
        for (const t of placed) { fuzzyCompare(t.width, 100, 0.001); fuzzyCompare(t.height, 50, 0.001); }
        const middleRight = Model.placedRects(row, [flat, Object.assign({}, flat, {x:1800}), flat])[1];
        fuzzyCompare(middleRight.x + middleRight.width / 2, 330, 0.001);
        const middleLeft = Model.placedRects(row, [flat, Object.assign({}, flat, {x:-1800}), flat])[1];
        fuzzyCompare(middleLeft.x + middleLeft.width / 2, 10, 0.001);
        // Invalid placements (old units out of range, non-integers) draw flat.
        const odd = Model.placedRects(row, [Object.assign({}, flat, {x:1801}), Object.assign({}, flat, {y:0.5}), null]);
        for (let i = 0; i < 3; ++i) { fuzzyCompare(odd[i].x, row[i].x, 0.001); fuzzyCompare(odd[i].y, row[i].y, 0.001); }
    }
    function test_placementEditorIdle() {
        const editor=createTemporaryObject(placementComponent,testCase,{width:640,placement:Model.defaultPlacement(),index:1});
        verify(editor !== null);
        verify(waitForRendering(editor));
        verify(!findChild(editor,"placementReset").enabled);
        editor.setField("curvature", 45.4);
        editor.setField("distance", 150);
        compare(editor.draft.curvature, 45);
        verify(findChild(editor,"placementReset").enabled);
        placementSpy.target=editor; placementSpy.clear();
        const slider=findChild(editor,"placement_x");
        mousePress(slider); mouseRelease(slider);  // idle: a release does not save on its own
        compare(placementSpy.count,0);
        mouseClick(findChild(editor,"placementSave"));
        compare(placementSpy.count,1);
        compare(placementSpy.signalArguments[0][0].curvature,45);
        compare(placementSpy.signalArguments[0][0], {x:0, y:0, distance:150, curvature:45});  // exactly the four fields
        editor.busy=true;
        verify(!findChild(editor,"placementSave").enabled);
        verify(!slider.enabled);
        editor.placement={x:5, y:0, distance:100, curvature:0};
        compare(editor.draft.x,5);
        compare(findChild(editor,"placementValue_x").text,"0.5°");
        compare(findChild(editor,"placementValue_y").text,"0°");
        compare(findChild(editor,"placementValue_distance").text,"100%");
        placementSpy.target=null;
    }
    function test_placementValueLabels() {
        // The orbit sliders span the new tenths of a degree, and the widest values fit their column at the panel's width.
        const widest={x:-1799, y:-899, distance:200, curvature:120};
        const editor=createTemporaryObject(placementComponent,testCase,{width:660,placement:widest});
        verify(waitForRendering(editor));
        compare(findChild(editor,"placement_x").from,-1800);
        compare(findChild(editor,"placement_x").to,1800);
        compare(findChild(editor,"placement_y").from,-900);
        compare(findChild(editor,"placement_y").to,900);
        compare(findChild(editor,"placement_x").value,-1799);
        const expected={x:"-179.9°", y:"-89.9°", distance:"200%", curvature:"120°"};
        for (const key of Object.keys(expected)) {
            const label=findChild(editor,"placementValue_" + key);
            verify(label !== null, key);
            compare(label.text, expected[key]);
            verify(label.width >= label.implicitWidth, key + " value clipped: " + label.width + " < " + label.implicitWidth);
        }
        // The Omarchy shell's font is monospace (JetBrainsMono Nerd Font), wider than the test's default sans.
        const xValue=findChild(editor,"placementValue_x");
        xValue.font.family="monospace";
        verify(xValue.width >= xValue.implicitWidth, "monospace -179.9° clipped: " + xValue.width + " < " + xValue.implicitWidth);
        editor.setField("x", -1800.4);  // a slider value rounds to a whole tenth
        compare(editor.draft.x,-1800);
        compare(findChild(editor,"placementValue_x").text,"-180°");
    }
    function test_placementEditorLive() {
        const editor=createTemporaryObject(placementComponent,testCase,{width:640,placement:Model.defaultPlacement(),live:true});
        verify(waitForRendering(editor));
        placementSpy.target=editor; placementSpy.clear();
        const slider=findChild(editor,"placement_distance");
        mousePress(slider); mouseRelease(slider);  // live: release applies
        compare(placementSpy.count,1);
        verify(Model.placementEdit(placementSpy.signalArguments[0][0]) !== null);
        editor.setField("curvature", 12);
        mouseClick(findChild(editor,"placementReset"));  // live: Flat applies immediately
        compare(placementSpy.count,2);
        verify(Model.isFlat(placementSpy.signalArguments[1][0]));
        verify(!findChild(editor,"placementReset").enabled);
        placementSpy.target=null;
    }
    function test_desktopEditorIsStartupResolutionOnly() {
        // Placement (and ultrawide, adding and removing desktops) is done in the glasses now: the pre-start editor keeps
        // only what cannot change once a session runs, the resolution and scale.
        const editor=createTemporaryObject(desktopComponent,testCase,{width:660,desktop:{width:1920,height:1080,scale:1}});
        verify(waitForRendering(editor));
        for (const key of ["x","y","distance","curvature"]) compare(findChild(editor,"placement_" + key), null);
        verify(findChild(editor,"scale1.25") !== null);
        verify(!("placement" in editor) && !("savePlacement" in editor));
    }
    function test_placementLabels() {
        compare(Model.placementLabel("x"), "Left / right");
        compare(Model.placementLabel("y"), "Down / up");
        compare(Model.placementLabel("distance"), "Distance");
        compare(Model.placementLabel("curvature"), "Curvature");
        // At the panel's editor width every label fits its column (no clipping or eliding).
        const editor=createTemporaryObject(placementComponent,testCase,{width:660,placement:Model.defaultPlacement()});
        verify(waitForRendering(editor));
        for (const key of ["x","y","distance","curvature"]) {
            const label=findChild(editor,"placementLabel_" + key);
            verify(label !== null, key);
            compare(label.text, Model.placementLabel(key));
            verify(label.width >= label.implicitWidth, key + " label clipped: " + label.width + " < " + label.implicitWidth);
            verify(findChild(editor,"placement_" + key).width >= 200, key + " slider squeezed");
        }
    }
    function test_placementEditorHasNoFacingSettings() {
        // Every desktop always faces you: four slider rows (the glass panel's kPlacementMenuFields), no turning rows.
        const editor=createTemporaryObject(placementComponent,testCase,{width:660,placement:Model.defaultPlacement()});
        verify(waitForRendering(editor));
        compare(findChild(editor,"placementRows").count, 4);
        for (const key of ["yaw", "pitch", "roll"]) {
            compare(Model.placementLabel(key), "", key);
            compare(findChild(editor,"placement_" + key), null, key);
            compare(findChild(editor,"placementLabel_" + key), null, key);
        }
        const hint=findChild(editor,"placementHint").text;
        verify(hint.indexOf("always faces you") >= 0, hint);
        verify(!/\b(yaw|pitch|roll)\b/i.test(hint), hint);
    }
}
