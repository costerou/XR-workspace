import QtQuick
import QtQuick.Controls
import QtTest
import qs.Commons as Commons
import ".." as Workspace

TestCase {
    id: testCase
    name: "MenuMotion"
    when: windowShown
    visible: true
    width: 700; height: 1000

    Component { id: launcherComponent; Workspace.Launcher {} }
    Component { id: comboComponent; Workspace.AnimatedComboBox { model: ["One", "Two", "Three"] } }
    Component {
        id: menuComponent
        Menu {
            popupType: Popup.Window
            opacity: 0
            scale: 0.97
            enter: Workspace.MenuTransition {}
            exit: Workspace.MenuTransition { entering: false }
            MenuItem { text: "Restart plugin" }
        }
    }
    function makeLauncher() {
        const item = createTemporaryObject(launcherComponent, testCase, {
            width: 660,
            snapshot: {state:"idle",model:"beast",fallback:"DP-1",fallback_candidates:["DP-1", "DP-2"]},
            settings: {desktops:[{width:1920,height:1080,scale:1}],tracking:true}
        });
        verify(item !== null);
        verify(waitForRendering(item));
        return item;
    }
    function init() { Commons.Style.reduceMotion = false; }
    function cleanup() { Commons.Style.reduceMotion = true; }

    function test_startupOptionsReverseWithoutAcceptingHiddenInput() {
        const launcher = makeLauncher();
        const options = findChild(launcher, "startupOptions");
        launcher.showOptions = true;
        tryVerify(() => options.opacity > 0 && options.opacity < 1);
        tryCompare(options, "opacity", 1);
        const expandedHeight = options.height;
        verify(expandedHeight > 100);
        launcher.showOptions = false;
        verify(!options.enabled, "closing controls must stop receiving input immediately");
        verify(options.visible, "retain the outgoing contents until their exit completes");
        tryVerify(() => options.height > 0 && options.height < expandedHeight);
        launcher.showOptions = true;
        verify(options.enabled);
        tryCompare(options, "opacity", 1);
        compare(options.height, expandedHeight);
        launcher.showOptions = false;
        tryCompare(options, "visible", false);
        compare(options.implicitHeight, 0);
    }

    function test_popupClosesAndReopensSmoothly() {
        const combo = createTemporaryObject(comboComponent, testCase, {width:200});
        verify(combo !== null);
        combo.popup.open();
        tryVerify(() => combo.popup.opacity > 0 && combo.popup.opacity < 1);
        tryCompare(combo.popup, "opened", true);
        compare(combo.popup.opacity, 1);
        compare(combo.popup.scale, 1);
        combo.popup.close();
        tryVerify(() => combo.popup.opacity > 0 && combo.popup.opacity < 0.9);
        const opacityBeforeReverse = combo.popup.opacity;
        combo.popup.open();
        verify(Math.abs(combo.popup.opacity - opacityBeforeReverse) < 0.05,
               "reopening should reverse from the current frame without flashing");
        tryCompare(combo.popup, "opened", true);
        compare(combo.popup.opacity, 1);
        combo.popup.close();
        tryCompare(combo.popup, "visible", false);
        combo.popup.open();
        tryCompare(combo.popup, "opened", true);
        compare(combo.popup.opacity, 1);
        compare(combo.popup.scale, 1);
        combo.popup.close();
        tryCompare(combo.popup, "visible", false);
    }

    function test_popupWithoutMotionStillSelectsWithKeyboard() {
        Commons.Style.reduceMotion = true;
        const combo = createTemporaryObject(comboComponent, testCase, {width:200});
        verify(combo !== null);
        combo.forceActiveFocus();
        keyClick(Qt.Key_Space);
        compare(combo.popup.opened, true);
        compare(combo.popup.opacity, 1);
        keyClick(Qt.Key_Down);
        keyClick(Qt.Key_Return);
        compare(combo.currentIndex, 1);
        compare(combo.popup.visible, false);
    }

    function test_windowPopupUsesTheSameTransition() {
        const menu = createTemporaryObject(menuComponent, testCase);
        verify(menu !== null);
        menu.open();
        tryVerify(() => menu.opacity > 0 && menu.opacity < 1);
        tryCompare(menu, "opened", true);
        compare(menu.opacity, 1);
        compare(menu.scale, 1);
        menu.close();
        tryVerify(() => menu.opacity > 0 && menu.opacity < 1);
        tryCompare(menu, "visible", false);
        Commons.Style.reduceMotion = true;
        menu.open();
        compare(menu.opened, true);
        compare(menu.opacity, 1);
        menu.close();
        compare(menu.visible, false);
    }

    function test_disablingMotionDuringEntranceSettles() {
        const launcher = makeLauncher();
        const options = findChild(launcher, "startupOptions");
        launcher.showOptions = true;
        tryVerify(() => options.opacity > 0 && options.opacity < 1);
        Commons.Style.reduceMotion = true;
        tryCompare(options, "opacity", 1, 100);
        launcher.showOptions = false;
        compare(options.visible, false);
    }

    function test_disabledMotionSettlesImmediately() {
        Commons.Style.reduceMotion = true;
        const launcher = makeLauncher();
        const options = findChild(launcher, "startupOptions");
        launcher.showOptions = true;
        compare(options.opacity, 1);
        verify(options.visible);
        launcher.showOptions = false;
        compare(options.opacity, 0);
        compare(options.visible, false);
    }
}
