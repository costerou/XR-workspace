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
        compare(Model.selectOperations(0,2,["next","prev"],3), ["next","next"]);
        compare(Model.selectOperations(2,0,["next","prev"],3), ["prev","prev"]);
        compare(Model.selectOperations(undefined,2,["next"],3), []);
        compare(Model.selectOperations(0,2,[],3), []);
        compare(Model.selectOperations(1,1,["next","prev"],3), []);
        // Any running count: XR-6 is reachable with six desktops, never beyond the run's count.
        compare(Model.selectOperations(0,5,["next","prev"],6), Array(5).fill("next"));
        compare(Model.selectOperations(0,3,["next","prev"],3), []);
        compare(Model.selectOperations(0,1,["next","prev"],1), []);
        compare(Model.selectOperations(0,1,["next","prev"],undefined), []);
        compare(Model.selectOperations(0,6,["next","prev"],7), []);
    }
    function test_desktopCount() {
        compare(Model.desktopLimits({}), {min:1,max:6});
        compare(Model.desktopLimits({desktop_limits:{min:1,max:4}}), {min:1,max:4});
        compare(Model.desktopLimits({desktop_limits:{min:5,max:4}}), {min:1,max:6});
        const a={width:1920,height:1080,scale:1}, b={width:3840,height:1080,scale:1}, c={width:2560,height:1440,scale:1};
        compare(Model.removeDesktop([a,b,c],1), [a,c]);
        compare(Model.removeDesktop([a,b],0,{min:1,max:6}), [b]);
        compare(Model.removeDesktop([a],0), null);          // never below the minimum
        compare(Model.removeDesktop([a,b],2), null);
        compare(Model.removeDesktop([a,b],-1), null);
        compare(Model.removeDesktop(undefined,0), null);
        verify(Model.largeLayoutWarning(Array(4).fill({width:1920,height:1080})));  // more than three 1080p is unmeasured
        verify(!Model.largeLayoutWarning([{width:1920,height:1080}]));
        const lines=Model.scopeLines({model:"beast",output:"DP-2",recovery_output:"DP-1",desktops:Array(6).fill(a),tracking:false,mode_switch:false},"start");
        compare(lines.filter(l => /^XR-\d: /.test(l.text)).length, 6);
        verify(Model.scopeLines({recovery_output:"DP-1",desktops:Array(6).fill(a)},"recover").some(l => l.text.indexOf("XR-1, XR-2, XR-3, XR-4, XR-5, XR-6.") >= 0));
    }
    SignalSpy { id:removeSpy; signalName:"remove" }
    function test_desktopRemoveButton() {
        const editor=createTemporaryObject(desktopComponent,testCase,{width:660,index:3,desktop:{width:1920,height:1080,scale:1}});
        verify(waitForRendering(editor));
        const remove=findChild(editor,"removeDesktop");
        compare(remove.text,"Remove XR-4");
        verify(!remove.enabled);                            // removable defaults to false (e.g. last remaining desktop)
        editor.removable=true;
        verify(remove.enabled);
        removeSpy.target=editor; removeSpy.clear();
        mouseClick(remove);
        compare(removeSpy.count,1);
        editor.busy=true;
        verify(!remove.enabled);
        removeSpy.target=null;
    }
}
