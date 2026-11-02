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
        editor.desktop={width:1920,height:1080,scale:2};
        compare(editor.draftWidth,1920);
        compare(editor.draftScale,2);
    }
    function test_scales() {
        compare(Model.validScales(1920, 1080), [1, 1.25, 1.5, 2])
        compare(Model.validScales(3440, 1440), [1, 1.25, 2])
        compare(Model.validScales(5120, 1440), [1, 1.25, 2])
        compare(Model.validScales(3840, 1080), [1, 1.25, 1.5, 2])
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
        verify(lines.some(l => l.text.indexOf("3840 × 1080 · 125%") >= 0));
        verify(lines.some(l => l.text.indexOf("DP-1") >= 0));
        verify(Model.scopeLines(scope, "recover").every(l => !l.optional));
        compare(Model.scopeLines(Object.assign({}, scope, {model:"beast", mode_switch:false}), "start").filter(l => l.key === "mode_switch").length, 0);
    }
    function test_desktopEdit() {
        compare(Model.desktopEdit(3840,1080,1.75), null);
        compare(Model.desktopEdit(641,1080,1), null);
        compare(Model.desktopEdit(7682,1080,1), null);
        compare(Model.desktopEdit(3440,1440,1.25), {width:3440,height:1440,scale:1.25});
    }
    function test_selectOperations() {
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
        const edge = {x:-100, y:100, distance:200, yaw:-60, pitch:45, roll:-45, curvature:120};
        compare(Model.placementEdit(edge), edge);
        for (const key of ["x","y","distance","yaw","pitch","roll","curvature"]) {
            const b = Model.placementBounds(key);
            compare(Model.placementEdit(Object.assign({}, flat, {[key]:b[0]-1})), null);
            compare(Model.placementEdit(Object.assign({}, flat, {[key]:b[1]+1})), null);
            compare(Model.placementEdit(Object.assign({}, flat, {[key]:0.5})), null);
        }
        compare(Model.placementEdit(Object.assign({}, flat, {z:0})), null);
        compare(Model.placementEdit(null), null);
        compare(Model.placementBounds("distance"), [50, 200]);
    }
    function test_withPlacement() {
        const tilt = {x:10, y:0, distance:100, yaw:20, pitch:0, roll:0, curvature:30};
        compare(Model.withPlacement(undefined, 1, tilt), [Model.defaultPlacement(), tilt, Model.defaultPlacement()]);
        compare(Model.withPlacement([tilt, {bad:1}], 2, tilt), [tilt, Model.defaultPlacement(), tilt]);
        compare(Model.withPlacement([tilt, tilt, tilt], 0, {x:999}), [tilt, tilt, tilt]);
        compare(Model.placementSummary(Model.defaultPlacement()), "flat, in line");
        verify(Model.placementSummary(tilt).indexOf("curved 30°") >= 0);
    }
    function test_placedRects() {
        const rects = [{x:0, y:0, width:100, height:50}];
        compare(Model.placedRects(rects, []), [{x:0, y:0, width:100, height:50, rotation:-0}]);
        const r = Model.placedRects(rects, [{x:50, y:20, distance:200, yaw:60, pitch:0, roll:10, curvature:0}])[0];
        fuzzyCompare(r.width, 25, 0.001);   // half size at 200%, cos(60°) foreshortening
        fuzzyCompare(r.height, 25, 0.001);
        fuzzyCompare(r.x + r.width / 2, 100, 0.001);  // centre offset by half its width
        fuzzyCompare(r.y + r.height / 2, 15, 0.001);  // +y is up
        compare(r.rotation, -10);
    }
    function test_placementEditorIdle() {
        const editor=createTemporaryObject(placementComponent,testCase,{width:640,placement:Model.defaultPlacement(),index:1});
        verify(editor !== null);
        verify(waitForRendering(editor));
        verify(!findChild(editor,"placementReset").enabled);
        editor.setField("curvature", 45.4);
        editor.setField("yaw", -20);
        compare(editor.draft.curvature, 45);
        verify(findChild(editor,"placementReset").enabled);
        placementSpy.target=editor; placementSpy.clear();
        const slider=findChild(editor,"placement_x");
        mousePress(slider); mouseRelease(slider);  // idle: a release does not save on its own
        compare(placementSpy.count,0);
        mouseClick(findChild(editor,"placementSave"));
        compare(placementSpy.count,1);
        compare(placementSpy.signalArguments[0][0].curvature,45);
        compare(placementSpy.signalArguments[0][0].yaw,-20);
        editor.busy=true;
        verify(!findChild(editor,"placementSave").enabled);
        verify(!slider.enabled);
        editor.placement={x:5, y:0, distance:100, yaw:0, pitch:0, roll:0, curvature:0};
        compare(editor.draft.x,5);
        placementSpy.target=null;
    }
    function test_placementEditorLive() {
        const editor=createTemporaryObject(placementComponent,testCase,{width:640,placement:Model.defaultPlacement(),live:true});
        verify(waitForRendering(editor));
        placementSpy.target=editor; placementSpy.clear();
        const slider=findChild(editor,"placement_distance");
        mousePress(slider); mouseRelease(slider);  // live: release applies
        compare(placementSpy.count,1);
        verify(Model.placementEdit(placementSpy.signalArguments[0][0]) !== null);
        editor.setField("roll", 12);
        mouseClick(findChild(editor,"placementReset"));  // live: Flat applies immediately
        compare(placementSpy.count,2);
        verify(Model.isFlat(placementSpy.signalArguments[1][0]));
        verify(!findChild(editor,"placementReset").enabled);
        placementSpy.target=null;
    }
    function test_desktopEditorPlacement() {
        const editor=createTemporaryObject(desktopComponent,testCase,{width:660,desktop:{width:1920,height:1080,scale:1},
            placement:{x:0, y:0, distance:150, yaw:0, pitch:0, roll:0, curvature:20}});
        verify(waitForRendering(editor));
        compare(findChild(editor,"placement_distance").value,150);
        compare(findChild(editor,"placement_curvature").value,20);
    }
}
