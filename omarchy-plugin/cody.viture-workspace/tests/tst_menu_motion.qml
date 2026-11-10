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
    function init() { Commons.Style.reduceMotion = false; }
    function cleanup() { Commons.Style.reduceMotion = true; }

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

}
