import QtQuick
import QtTest
import "../Model.js" as Model
import ".." as Workspace

// The pre-start panel (Launcher.qml): one big Start, the project logo, and the startup-only settings hidden under
// "Startup options" until asked for. Start still only asks the controller to begin (op begin_start); the one-run
// consent box (ConfirmDialog, tst_model.qml) is unchanged.
TestCase {
    id: testCase
    name: "PrestartLauncher"
    when: windowShown
    visible: true
    width: 700; height: 860

    readonly property var desktops: [{width:1920,height:1080,scale:1}, {width:1920,height:1080,scale:1}, {width:2560,height:1080,scale:1}]
    function idle(extra) {
        return Object.assign({state:"idle", model:"luma-ultra", output:"DP-2", fallback:"DP-1", fallback_candidates:["DP-1"]}, extra || {});
    }
    function settings(extra) {
        return Object.assign({desktops:desktops, tracking:true, mode_switch:true, fallback:null}, extra || {});
    }

    Component { id: launcherComponent; Workspace.Launcher {} }
    Component { id: logoComponent; Workspace.Logo {} }
    SignalSpy { id: requestSpy; signalName: "request" }

    function make(snapshot, prefs) {
        const launcher = createTemporaryObject(launcherComponent, testCase, {width:660, snapshot:snapshot || idle(), settings:prefs || settings()});
        verify(launcher !== null);
        verify(waitForRendering(launcher));
        requestSpy.clear();
        requestSpy.target = launcher;
        return launcher;
    }
    function cleanup() { requestSpy.target = null; }

    readonly property var drivers: [{key:"xrdriver", label:"Standard", path:"/usr/bin/xrDriver", available:true},
                                    {key:"3dof", label:"3DoF test", path:"/t/xrDriver", available:true},
                                    {key:"luma", label:"New driver", path:"/t/xr-luma-driver", available:true}]
    function openOptions(launcher) { launcher.showOptions = true; verify(waitForRendering(launcher)); }
    function test_trackingDriverOptionSavesThePathAndShowsTheSavedOne() {
        const launcher = make(idle({tracking_drivers:drivers}), settings({tracking_driver:"3dof"}));
        openOptions(launcher);
        verify(findChild(launcher, "trackingDriverOption").visible);
        const combo = findChild(launcher, "trackingDriverCombo");
        compare(combo.count, 3);
        compare(combo.currentText, "3DoF test");
        combo.currentIndex = 0;
        combo.activated(0);
        compare(requestSpy.count, 1);
        compare(requestSpy.signalArguments[0][0].op, "set_settings");
        compare(requestSpy.signalArguments[0][0].settings.tracking_driver, "xrdriver");
    }
    function test_trackingDriverOptionHiddenWithNothingToChoose() {
        const missing = [drivers[0], Object.assign({}, drivers[1], {available:false}), Object.assign({}, drivers[2], {available:false})];
        for (const snapshot of [idle(), idle({tracking_drivers:missing})]) {
            const launcher = make(snapshot, settings({tracking_driver:"xrdriver"}));
            openOptions(launcher);
            verify(!findChild(launcher, "trackingDriverOption").visible);
        }
    }
    function test_trackingDriverOptionHiddenWithHeadTrackingOff() {
        const launcher = make(idle({tracking_drivers:drivers}), settings({tracking:false, tracking_driver:"xrdriver"}));
        openOptions(launcher);
        verify(!findChild(launcher, "trackingDriverOption").visible);
    }
    function test_oneBigStartButton() {
        const launcher = make();
        const start = findChild(launcher, "startButton");
        verify(start !== null);
        verify(start.visible && start.enabled);
        compare(start.text, "Start XR");
        verify(start.height >= 64, "Start is " + start.height + " px tall");
        verify(start.width >= 400, "Start is " + start.width + " px wide");
        // It is the one button in view: everything else is collapsed or a hint.
        for (const name of ["startupOptions", "trackingOption", "modeSwitchOption", "fallbackOption", "desktopPreview"])
            verify(!findChild(launcher, name).visible, name + " shows before it is asked for");
        verify(!findChild(launcher, "fallbackRequired").visible);
        compare(launcher.showOptions, false);
        compare(findChild(launcher, "startStatus").text, "Luma Ultra on DP-2");
        verify(/in the glasses/.test(findChild(launcher, "inXrHint").text));
        compare(requestSpy.count, 0);
        mouseClick(start);
        compare(requestSpy.count, 1);
        compare(requestSpy.signalArguments[0][0], {op:"begin_start"});
    }

    function test_busyDisablesStart() {
        const launcher = make();
        launcher.busy = true;
        verify(!findChild(launcher, "startButton").enabled);
        mouseClick(findChild(launcher, "startButton"));
        compare(requestSpy.count, 0);
    }

    function test_returnMonitorChoiceStaysInViewWhenStartNeedsIt() {
        // begin_start refuses fallback "choose" (tools/xr_controller/state.py): the pick is shown next to Start, which
        // waits for it, instead of hiding under Startup options.
        const launcher = make(idle({fallback:"choose", fallback_candidates:["DP-1", "HDMI-A-1"]}));
        const row = findChild(launcher, "fallbackRequired");
        verify(row.visible);
        verify(!findChild(launcher, "startButton").enabled);
        verify(!findChild(launcher, "fallbackOption").visible);
        const combo = findChild(row, "fallbackCombo");
        verify(combo !== null);
        compare(combo.count, 2);
        combo.currentIndex = 1;
        combo.activated(1);
        compare(requestSpy.count, 1);
        compare(requestSpy.signalArguments[0][0], {op:"set_settings", settings:{fallback:"HDMI-A-1"}});
    }

    function test_noReturnMonitorBlocksStart() {
        const launcher = make(idle({fallback:null, fallback_candidates:[]}));
        verify(!findChild(launcher, "startButton").enabled);
        verify(findChild(launcher, "noFallback").visible);
    }

    function test_olderSnapshotWithoutFallbackStillStarts() {
        const launcher = make({state:"idle", model:"beast", output:"DP-2"});
        verify(findChild(launcher, "startButton").enabled);
        verify(!findChild(launcher, "noFallback").visible);
        compare(findChild(launcher, "startStatus").text, "Beast on DP-2");
    }

    function test_startupOptionsOpenOnRequest() {
        const launcher = make(idle({fallback_candidates:["DP-1", "HDMI-A-1"]}));
        const toggle = findChild(launcher, "startupOptionsToggle");
        verify(toggle.visible);
        mouseClick(toggle);
        compare(launcher.showOptions, true);
        waitForItemPolished(launcher);
        for (const name of ["startupOptions", "trackingOption", "modeSwitchOption", "fallbackOption", "desktopPreview"])
            verify(findChild(launcher, name).visible, name + " missing from Startup options");
        compare(findChild(launcher, "desktopTiles").count, 3);
        // The options still save through the controller, one request each.
        mouseClick(findChild(launcher, "trackingOption"));
        compare(requestSpy.count, 1);
        compare(requestSpy.signalArguments[0][0], {op:"set_settings", settings:{tracking:false}});
        mouseClick(findChild(launcher, "modeSwitchOption"));
        compare(requestSpy.signalArguments[1][0], {op:"set_settings", settings:{mode_switch:false}});
        // A desktop opens its startup resolution editor.
        compare(launcher.selected, -1);
        launcher.selected = 2;
        waitForItemPolished(launcher);
        const editor = findChild(launcher, "desktopEditor");
        verify(editor !== null && editor.visible);
        compare(editor.draftWidth, 2560);
        // Closing hides them again; Start is still the one way on.
        mouseClick(toggle);
        compare(launcher.showOptions, false);
        verify(!findChild(launcher, "trackingOption").visible);
        verify(findChild(launcher, "startButton").enabled);
    }

    function test_trackingDriverIsOfferedOnceInstalled() {
        // Only the standard driver installed: nothing to pick.
        let launcher = make(idle({luma_available:false, tracking_drivers:[drivers[0]]}), settings({tracking_driver:"xrdriver"}));
        launcher.showOptions = true;
        waitForItemPolished(launcher);
        verify(!findChild(launcher, "trackingDriverOption").visible);
        verify(!findChild(launcher, "lumaMissing").visible);
        // Installed: a pick, showing what is saved, and it saves through the controller.
        launcher = make(idle({luma_available:true, tracking_drivers:[drivers[0], drivers[2]]}), settings({tracking_driver:"xrdriver"}));
        launcher.showOptions = true;
        waitForItemPolished(launcher);
        verify(findChild(launcher, "trackingDriverOption").visible);
        const combo = findChild(launcher, "trackingDriverCombo");
        compare(combo.currentText, "Standard");
        combo.currentIndex = 1;
        combo.activated(1);
        compare(requestSpy.count, 1);
        compare(requestSpy.signalArguments[0][0], {op:"set_settings", settings:{tracking_driver:"luma"}});
        // Tracking off: no driver to pick.
        launcher = make(idle({luma_available:true}), settings({tracking:false, tracking_driver:"luma"}));
        launcher.showOptions = true;
        waitForItemPolished(launcher);
        verify(!findChild(launcher, "trackingDriverOption").visible);
        // Chosen, then a build without it was installed: the choice still shows and says what a start will do.
        launcher = make(idle({luma_available:false, tracking_drivers:[drivers[0], Object.assign({}, drivers[2], {available:false})]}), settings({tracking_driver:"luma"}));
        launcher.showOptions = true;
        waitForItemPolished(launcher);
        compare(findChild(launcher, "trackingDriverCombo").currentText, "New driver (missing)");
        verify(findChild(launcher, "lumaMissing").visible);
    }

    function test_desktopPreviewTilesDraw() {
        // A tile's negative z (bigger behind) once put every tile under the preview's own fill, out of sight.
        const launcher = make();
        launcher.showOptions = true;
        waitForItemPolished(launcher);  // the layout places the options first; a grab before that sees the old page
        const preview = findChild(launcher, "desktopPreview");
        const tile = findChild(launcher, "desktopTiles").itemAt(0);
        verify(tile.width > 20 && tile.height > 20);
        const shot = grabImage(launcher);
        function at(item, x, y) { const p = item.mapToItem(launcher, x, y); return shot.pixel(Math.round(p.x), Math.round(p.y)); }
        verify(Qt.colorEqual(at(preview, preview.width / 2, 6), preview.color), "preview background " + at(preview, preview.width / 2, 6));
        verify(!Qt.colorEqual(at(tile, 1, tile.height / 2), preview.color), "tile border hidden under the preview");
        verify(Qt.colorEqual(at(tile, 1, tile.height / 2), tile.border.color), "tile border " + at(tile, 1, tile.height / 2));
    }

    function test_heroLogo() {
        const launcher = make();
        const logo = findChild(launcher, "heroLogo");
        verify(logo !== null && logo.visible);
        compare(logo.size, 96);
        verify(logo.y + logo.height <= findChild(launcher, "startButton").y, "logo sits above Start");
    }

    function test_modeSwitchOnlyForLumaUltra() {
        const launcher = make(idle({model:"beast"}));
        launcher.showOptions = true;
        waitForItemPolished(launcher);
        verify(!findChild(launcher, "modeSwitchOption").visible);
        verify(findChild(launcher, "beastNote").visible);
    }

    function test_desktopRequestedOnOpenShowsItsEditor() {
        // Panel.open({desktop:n}) preselects a desktop: its editor is under Startup options, so they open with it.
        const launcher = createTemporaryObject(launcherComponent, testCase, {width:660, snapshot:idle(), settings:settings(), selected:1});
        verify(waitForRendering(launcher));
        compare(launcher.showOptions, true);
        verify(findChild(launcher, "desktopEditor").visible);
    }

    function test_startStatus() {
        compare(Model.glassesName("luma-ultra"), "Luma Ultra");
        compare(Model.glassesName("beast"), "Beast");
        compare(Model.glassesName("unknown"), "Glasses");
        compare(Model.glassesName(undefined), "Glasses");
        compare(Model.startStatus({model:"luma-ultra", output:"DP-2"}), "Luma Ultra on DP-2");
        compare(Model.startStatus({model:"beast"}), "Beast");
        compare(Model.startStatus({}), "Glasses");
        compare(Model.startStatus(undefined), "Glasses");
    }

    function test_logo() {
        const logo = createTemporaryObject(logoComponent, testCase, {size:64});
        verify(logo !== null);
        const emblem = findChild(logo, "logoEmblem");
        tryCompare(emblem, "status", Image.Ready);
        compare(emblem.width, 64);
        compare(emblem.height, 64);
        compare(findChild(logo, "logoTitle").text + " " + findChild(logo, "logoSubtitle").text, "XR Workspace");
        compare(logo.name, "XR Workspace");
        verify(waitForRendering(logo));
        // The SVG really draws (the 128-unit emblem at half size): the dark navy tile above the arcs, the bright
        // centre arc (logo-emblem.svg, about y 39..48 at x 64) below it.
        const shot = grabImage(emblem);
        const tile = shot.pixel(32, 10);
        verify(tile.a > 0.9 && tile.b > tile.r && tile.r < 0.3, "tile " + tile);
        const arc = shot.pixel(32, 22);
        verify(arc.a > 0.9 && arc.b > 0.6, "arc " + arc);
    }

    // The Pro 2 with its route on: its own projection, labelled approximate, and nothing of the other models' startup options.
    function test_pro2ProjectionRowOnlyForAPro2WithTheRoute() {
        const pro2 = {projection:44.5, choices:[44.5,45.5,46.5,47.5,48.5,49.5,50], label:"approximate"};
        const launcher = make(idle({model:"pro2", pro2:pro2}), settings({tracking:false, mode_switch:false}));
        launcher.showOptions = true;
        verify(waitForRendering(launcher));
        verify(findChild(launcher, "pro2ProjectionOption").visible);
        verify(!findChild(launcher, "trackingOption").visible);
        verify(!findChild(launcher, "modeSwitchOption").visible);
        const combo = findChild(launcher, "pro2ProjectionCombo");
        compare(combo.count, 7);
        compare(combo.currentIndex, 0);
        verify(combo.currentText.indexOf("approximate") >= 0, combo.currentText);
        verify(findChild(launcher, "pro2Note").text.indexOf("not calibrated") >= 0);
        combo.activated(3);
        compare(requestSpy.count, 1);
        compare(requestSpy.signalArguments[0][0].op, "set_pro2_projection");
        compare(requestSpy.signalArguments[0][0].projection, 47.5);
        const luma = make(idle(), settings());
        luma.showOptions = true;
        verify(waitForRendering(luma));
        verify(!findChild(luma, "pro2ProjectionOption").visible);
        verify(findChild(luma, "trackingOption").visible);
    }
}
