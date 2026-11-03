import QtQuick
import QtQuick.Controls
import QtQuick.Effects
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
        tooltipText:client.connected ? "XR Workspace · " + (client.snapshot.state || "waiting") : "controller not running"
        onPressed: function(mouseButton) {
            if (mouseButton === Qt.RightButton) contextMenu.popup(button, 0, button.height)
            else if (mouseButton === Qt.LeftButton && root.host) root.host.run("omarchy-shell shell toggle cody.viture-workspace")
        }
        // The app mark, tinted by state like the old "XR" label: accent while
        // running, warning when recovery is needed, muted otherwise.
        readonly property string role:Model.barColorRole(client.snapshot.state)
        readonly property color tint:role==="accent" ? Commons.Color.accent : role==="warning" ? "#e7c78a" : Commons.Color.muted
        Image {
            id:mark
            anchors.centerIn:parent
            height:Style.bar.iconSize+2
            width:Math.round(height*120/84)
            sourceSize.width:width*2; sourceSize.height:height*2
            source:Qt.resolvedUrl("icon-symbolic.svg")
            fillMode:Image.PreserveAspectFit
            smooth:true
            visible:false
            layer.enabled:true
        }
        MultiEffect {
            anchors.fill:mark
            source:mark
            colorization:1.0
            colorizationColor:button.tint
        }
        Rectangle {
            visible:button.role==="warning"
            width:5; height:5; radius:2.5
            color:"#e7c78a"
            anchors.left:mark.right; anchors.top:mark.top
            anchors.leftMargin:-1; anchors.topMargin:-2
        }
    }
}
