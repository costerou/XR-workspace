pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import qs.Commons
import qs.Ui as Ui
import "Model.js" as Model

// The one-run consent that follows Start or Recover (docs/mockups/plugin-prestart 04): every scope line, grouped under
// plain headings. Only the start's head tracking and 120 Hz switch are controls, and they can only reduce the scope.
// Without a pending scope (it lapsed while the page was open) the page says so and offers the way back.
PageFrame {
    id: root
    required property var pending
    property bool busy: false
    signal request(var message)
    readonly property bool recover: !!pending && pending.action === "recover"
    property bool tracking: !!pending && !!pending.scope.tracking
    property bool modeSwitch: !!pending && !!pending.scope.mode_switch
    onPendingChanged: { tracking = !!pending && !!pending.scope.tracking; modeSwitch = !!pending && !!pending.scope.mode_switch }
    footerVisible: !!pending

    function confirm() {
        const m = {op: recover ? "confirm_recover" : "confirm_start", scope_id: pending.scope_id}
        if (!recover) m.optional = {tracking: tracking, mode_switch: modeSwitch}
        request(m)
    }

    // The scope.
    ColumnLayout {
        visible: !!root.pending
        Layout.fillWidth: true
        spacing: 14
        ColumnLayout {
            spacing: 2
            PanelText {
                objectName: "confirmTitle"
                text: root.recover ? "Recover the last session?" : "Start XR on " + Model.glassesName(root.pending ? root.pending.scope.model : "") + "?"
                font.pixelSize: 17; font.bold: true
            }
            PanelText { text: "Your approval covers this one session."; alpha: 0.55; font.pixelSize: 11 }
        }
        GridLayout {
            columns: 2
            columnSpacing: 18
            rowSpacing: 12
            Layout.fillWidth: true
            Repeater {
                model: root.pending ? Model.confirmGroups(root.pending.scope, root.pending.action, root.pending.rules) : []
                delegate: SectionLabel {
                    required property var modelData
                    required property int index
                    text: modelData.label
                    Layout.row: index; Layout.column: 0
                    Layout.alignment: Qt.AlignTop
                    Layout.topMargin: 2
                    Layout.preferredWidth: 78
                }
            }
            Repeater {
                model: root.pending ? Model.confirmGroups(root.pending.scope, root.pending.action, root.pending.rules) : []
                delegate: ColumnLayout {
                    id: group
                    required property var modelData
                    required property int index
                    Layout.row: index; Layout.column: 1
                    Layout.fillWidth: true
                    spacing: 6
                    Repeater {
                        model: group.modelData.lines
                        delegate: ColumnLayout {
                            id: entry
                            required property var modelData
                            Layout.fillWidth: true
                            spacing: 6
                            RowLayout {
                                spacing: 12
                                PanelText {
                                    text: entry.modelData.text
                                    alpha: !entry.modelData.optional || (entry.modelData.key === "tracking" ? root.tracking : root.modeSwitch) ? 1 : 0.5
                                }
                                Ui.ToggleSwitch {
                                    objectName: entry.modelData.key
                                    visible: entry.modelData.optional
                                    Layout.alignment: Qt.AlignTop
                                    checked: entry.modelData.key === "tracking" ? root.tracking : root.modeSwitch
                                    interactive: !root.busy
                                    onToggled: { if (entry.modelData.key === "tracking") root.tracking = !root.tracking; else root.modeSwitch = !root.modeSwitch }
                                }
                            }
                            Flow {
                                visible: entry.modelData.chips.length > 0
                                Layout.fillWidth: true
                                spacing: 6
                                Repeater {
                                    model: entry.modelData.chips
                                    delegate: Rectangle {
                                        required property string modelData
                                        width: chip.implicitWidth + 14; height: chip.implicitHeight + 6
                                        color: "transparent"; border.width: 1; border.color: Tone.a(0.18)
                                        PanelText { id: chip; anchors.centerIn: parent; width: implicitWidth; text: parent.modelData; font.pixelSize: 11; alpha: 0.75; wrapMode: Text.NoWrap }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        Item { Layout.fillHeight: true }
    }

    // The scope lapsed.
    Item { visible: !root.pending; Layout.fillHeight: true }
    ColumnLayout {
        objectName: "expired"
        visible: !root.pending
        Layout.fillWidth: true
        spacing: 10
        PanelText { text: "This request expired"; font.pixelSize: 16; font.bold: true; horizontalAlignment: Text.AlignHCenter }
        PanelText {
            text: "Something changed while the confirmation was open, for example a monitor or the glasses. Nothing was started."
            alpha: 0.55; font.pixelSize: 11; horizontalAlignment: Text.AlignHCenter
            Layout.leftMargin: 30; Layout.rightMargin: 30
        }
        Ui.Button {
            objectName: "backToStart"
            Layout.alignment: Qt.AlignHCenter
            Layout.topMargin: 6
            text: "Back to start"
            background: Tone.accent; foreground: Tone.bg
            enabled: !root.busy
            onClicked: root.request({op: "cancel"})
        }
    }
    Item { visible: !root.pending; Layout.fillHeight: true }

    footer: [
        PanelText { text: root.pending ? Model.confirmFooter(root.pending.scope, root.pending.action) : ""; alpha: 0.55; font.pixelSize: 11 },
        Ui.Button { objectName: "cancelScope"; text: "Cancel"; bordered: true; enabled: !root.busy; onClicked: root.request({op: "cancel"}) },
        Ui.Button {
            objectName: "confirmScope"
            text: root.recover ? "Recover" : "Start"
            Layout.preferredWidth: 110
            background: Tone.accent; foreground: Tone.bg
            enabled: !root.busy && !!root.pending
            opacity: enabled ? 1 : 0.5
            onClicked: root.confirm()
        }
    ]
}
