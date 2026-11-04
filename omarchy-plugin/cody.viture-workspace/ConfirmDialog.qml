pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "Model.js" as Model

ColumnLayout {
    id: root
    required property var pending
    property bool busy: false
    property bool tracking: !!pending.scope.tracking
    property bool modeSwitch: !!pending.scope.mode_switch
    signal confirm(var optional)
    signal cancel()
    spacing: 10
    Label { text: root.pending.action === "recover" ? "Recover this session?" : "Start XR session?"; font.bold: true; font.pixelSize: 20 }
    Label { text: "Your approval applies to this one run." }
    Repeater {
        model: Model.scopeLines(root.pending.scope, root.pending.action, root.pending.rules)
        delegate: CheckBox {
            required property var modelData
            id: scopeCheck
            objectName:modelData.key || "requiredScope"
            Layout.fillWidth: true
            text: modelData.text
            checked: modelData.optional ? (modelData.key === "tracking" ? root.tracking : root.modeSwitch) : !modelData.unavailable
            enabled: modelData.optional && !root.busy
            contentItem: Label { text: scopeCheck.text; wrapMode: Text.WordWrap; leftPadding: 30; opacity: scopeCheck.enabled || !scopeCheck.modelData.optional ? 1 : 0.6 }
            onToggled: { if (modelData.key === "tracking") root.tracking = checked; else root.modeSwitch = checked }
        }
    }
    Label { text: "Required scope cannot be unticked. Optional choices can only reduce this scope."; wrapMode: Text.WordWrap; Layout.fillWidth: true; opacity: 0.65 }
    RowLayout {
        Button { text: "Cancel"; enabled: !root.busy; onClicked: root.cancel() }
        Item { Layout.fillWidth: true }
        Button { objectName:"confirmScope"; text: root.pending.action === "recover" ? "Recover" : "Start"; highlighted: true; enabled: !root.busy; onClicked: root.confirm({tracking:root.tracking, mode_switch:root.modeSwitch}) }
    }
}
