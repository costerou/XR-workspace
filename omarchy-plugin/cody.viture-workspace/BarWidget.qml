import QtQuick
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
    Ui.WidgetButton {
        id:button
        bar:root.bar
        anchors.fill:parent
        text: ""
        labelVisible:false
        hasVisualContent:true
        fixedWidth:Style.space(56)
        tooltipText:client.connected ? "VITURE Workspace · " + (client.snapshot.state || "waiting") : "controller not running"
        onPressed: { if(root.host) root.host.run("omarchy-shell shell toggle cody.viture-workspace") }
        Text {
            anchors.centerIn:parent
            text:Model.barColorRole(client.snapshot.state)==="warning" ? "XR !" : "XR"
            font.family:root.themeFont.family; font.pixelSize:root.themeFont.body
            color:Model.barColorRole(client.snapshot.state)==="accent" ? Commons.Color.accent : Model.barColorRole(client.snapshot.state)==="warning" ? "#e7c78a" : Commons.Color.muted
        }
    }
}
