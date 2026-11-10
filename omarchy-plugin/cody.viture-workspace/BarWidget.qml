import QtQuick
import QtQuick.Controls
import Quickshell
import qs.Commons
import qs.Commons as Commons
import qs.Ui as Ui
import "Model.js" as Model

Ui.BarWidget {
    id:root
    moduleName:"cody.viture-workspace"
    implicitWidth:button.implicitWidth
    implicitHeight:button.implicitHeight
    property alias socketPath:client.socketPath
    readonly property var host:bar
    readonly property var themeFont:Style.font
    ControllerClient { id:client }
    Menu {
        id: contextMenu
        popupType: Popup.Window
        opacity: 0
        scale: 0.97
        enter: MenuTransition {}
        exit: MenuTransition { entering: false }
        MenuItem {
            text: "Open XR Workspace"
            onTriggered: if (root.host) root.host.run("omarchy-shell shell toggle cody.viture-workspace")
        }
        MenuItem {
            text: "Restart plugin"
            onTriggered: Quickshell.execDetached(["bash", Qt.resolvedUrl("restart.sh").toString().replace(/^file:\/\//, "")])
        }
    }
    Ui.WidgetButton {
        id:button
        bar:root.bar
        anchors.fill:parent
        text: ""
        labelVisible:false
        hasVisualContent:true
        fixedWidth:Style.space(56)
        tooltipText:Model.barTooltip(client.connected, client.snapshot)
        onPressed: function(mouseButton) {
            if (mouseButton === Qt.RightButton) contextMenu.popup(button, 0, button.height)
            else if (mouseButton === Qt.LeftButton && root.host) root.host.run("omarchy-shell shell toggle cody.viture-workspace")
        }
        // The app mark, tinted by state: full foreground when glasses are ready to start, accent while a session
        // starts, runs or stops, warning when something needs attention, muted otherwise.
        readonly property string role:client.connected ? Model.barColorRole(client.snapshot.state, client.snapshot.output) : "muted"
        readonly property color tint:role==="accent" ? Commons.Color.accent : role==="warning" ? Tone.warn
            : role==="foreground" ? Commons.Color.foreground : Commons.Color.muted
        Emblem {
            id:mark
            objectName:"barMark"
            anchors.centerIn:parent
            height:Style.bar.iconSize+2
            width:Math.round(height*120/84)
            color:button.tint
        }
        Rectangle {
            visible:button.role==="warning"
            width:5; height:5; radius:2.5
            color:Tone.warn
            anchors.left:mark.right; anchors.top:mark.top
            anchors.leftMargin:-1; anchors.topMargin:-2
        }
    }
}
