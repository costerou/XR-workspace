pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import qs.Ui as Ui
import "Model.js" as Model

// The page while a session runs (docs/mockups/plugin-poststart): the state, one Stop, Recenter, one health line and what is
// wrong. Every session option is in the glasses (the gear menu, the glass pills, the keys); this page stays the way out when
// the glasses' picture or tracking is unusable, so nothing here depends on seeing them.
PageFrame {
    id: root
    required property var snapshot
    property bool busy: false
    signal request(var message)

    readonly property bool problem: Model.runningProblem(snapshot)
    readonly property var reason: Model.reasonText(snapshot.reason || "")
    readonly property var incident: Model.incidentNote(snapshot)

    DeviceCard { objectName: "deviceCard"; info: Model.runningCard(root.snapshot) }
    Note {
        objectName: "reasonNote"
        visible: !!root.reason
        tone: "warn"
        title: root.reason ? root.reason.title : ""
        text: root.reason ? root.reason.text : ""
    }
    BigButton {
        objectName: "stopButton"
        text: "Stop XR"
        enabled: !root.busy
        onClicked: root.request({op: "stop"})
    }
    Ui.Button {
        objectName: "recenterButton"
        // Also in the glasses (Ctrl+Alt+Space), but this is the way back when the desktops are out of sight.
        visible: (root.snapshot.capabilities || []).indexOf("recenter") >= 0
        Layout.alignment: Qt.AlignHCenter
        text: "Recenter"
        bordered: true
        enabled: !root.busy
        opacity: enabled ? 1 : 0.5
        onClicked: root.request({op: "view_op", operation: "recenter"})
    }
    PanelText {
        objectName: "healthLine"
        text: Model.healthLine(root.snapshot)
        tone: root.problem ? "warn" : "fg"
        alpha: root.problem ? 1 : 0.65
        font.pixelSize: 11
        horizontalAlignment: Text.AlignHCenter
    }
    Note {
        objectName: "incidentNote"
        visible: !!root.incident
        tone: "quiet"
        title: root.incident ? root.incident.title : ""
        text: root.incident ? root.incident.text : ""
    }

    Item { Layout.fillHeight: true }

    footer: PanelText {
        objectName: "footerText"
        alpha: root.problem ? 0.85 : 0.55; font.pixelSize: 11
        text: Model.runningFooter(root.snapshot)
    }
}
