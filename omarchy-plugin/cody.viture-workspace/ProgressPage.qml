pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import qs.Commons
import qs.Ui as Ui
import "Model.js" as Model

// Between the confirmation and the session, and on the way back out (docs/mockups/plugin-prestart 05): starting with its
// steps (state.py start_steps / start_phase), stopping, and a start that failed.
PageFrame {
    id: root
    required property var snapshot
    property bool busy: false
    signal request(var message)
    readonly property string state: snapshot.state || ""
    readonly property var steps: Model.startSteps(snapshot)
    readonly property var failure: Model.reasonText(snapshot.reason)

    DeviceCard { objectName: "deviceCard"; info: Model.deviceCard(root.snapshot, true) }
    PageHeading {
        title: root.state === "starting" ? "Starting XR…" : root.state === "stopping" ? "Stopping XR…" : "XR did not start"
        text: root.state === "starting" ? "Put the glasses on. Your windows move when the desktops are ready."
            : root.state === "stopping" ? "Moving your windows back to " + (root.snapshot.fallback || "your monitor") + " and removing the XR desktops."
            : "Nothing is running in the glasses."
    }
    // Indeterminate: the controller does not report how far a step is.
    Rectangle {
        objectName: "progressBar"
        visible: root.state !== "failed"
        Layout.fillWidth: true
        Layout.preferredHeight: 3
        color: Tone.a(0.12)
        clip: true
        Rectangle {
            id: sweep
            width: parent.width * 0.35; height: parent.height
            color: Tone.fg
            NumberAnimation on x { from: -sweep.width; to: sweep.parent.width; duration: 1600; loops: Animation.Infinite; running: sweep.visible }
        }
    }
    ColumnLayout {
        objectName: "startSteps"
        visible: root.state === "starting" && root.steps.length > 0
        Layout.topMargin: 6
        spacing: 10
        Repeater {
            model: root.steps
            delegate: RowLayout {
                id: step
                required property var modelData
                objectName: "step:" + modelData.key
                spacing: 10
                Rectangle {
                    Layout.preferredWidth: 14; Layout.preferredHeight: 14
                    Layout.alignment: Qt.AlignTop
                    Layout.topMargin: 1
                    color: step.modelData.status === "done" ? Tone.a(0.18) : "transparent"
                    border.width: 1
                    border.color: step.modelData.status === "now" ? Tone.fg : Tone.a(step.modelData.status === "done" ? 0.5 : 0.25)
                    Text { anchors.centerIn: parent; visible: step.modelData.status === "done"; text: "✓"; color: Tone.fg; font.pixelSize: 10 }
                    Rectangle { anchors.centerIn: parent; visible: step.modelData.status === "now"; width: 6; height: 6; color: Tone.fg }
                }
                ColumnLayout {
                    spacing: 2
                    PanelText { text: step.modelData.label; alpha: step.modelData.status === "todo" ? 0.4 : 1 }
                    PanelText { visible: text !== ""; text: step.modelData.note; alpha: 0.55; font.pixelSize: 11 }
                }
            }
        }
    }
    Note {
        objectName: "failureNote"
        visible: root.state === "failed"
        tone: "err"
        title: "What happened"
        text: root.failure ? [root.failure.title, root.failure.text].filter(t => t).join(" ") : "The controller did not say."
    }
    RowLayout {
        visible: root.state === "failed" && !!root.snapshot.run_id
        spacing: 12
        Ui.Button {
            objectName: "cleanUp"
            text: "Clean up and go back"
            background: Tone.accent; foreground: Tone.bg
            enabled: !root.busy
            onClicked: root.request({op: "stop"})
        }
    }
    PanelText {
        visible: root.state === "failed" && !!root.snapshot.run_id
        text: "Removes anything this attempt left behind, then returns to Start."
        alpha: 0.55; font.pixelSize: 11
    }
    Item { Layout.fillHeight: true }

    footer: [
        Ui.Button {
            objectName: "cancelStart"
            visible: root.state === "starting"
            text: "Cancel start"
            bordered: true
            enabled: !root.busy
            onClicked: root.request({op: "stop"})
        },
        PanelText {
            alpha: 0.55; font.pixelSize: 11
            text: root.state === "starting" ? "Stops here and puts your windows back."
                : root.state === "stopping" ? "No action needed. This page returns to Start when it is done."
                : "Details: journalctl --user -u viture-workspace-controller"
        }
    ]
}
