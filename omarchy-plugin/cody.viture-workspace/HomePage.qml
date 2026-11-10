pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import qs.Commons as Commons
import qs.Ui as Ui
import "Model.js" as Model

// The page the panel opens on before a session (docs/mockups/plugin-prestart 01, 02): which glasses, one Start, what
// stands in its way, and the session it would start with. Every setting lives on Setup; Start only asks the controller to
// begin (op begin_start), and the one-run confirmation follows.
PageFrame {
    id: root
    required property var snapshot
    required property var settings
    property bool connected: true
    property bool busy: false
    property string error: ""
    property bool serviceRunning: false
    property string serviceError: ""
    property bool serviceTried: false
    signal request(var message)
    signal openSetup()
    signal startService()
    signal dismissError()

    readonly property var blocker: connected ? Model.startBlocker(snapshot, settings) : null
    readonly property var incident: connected ? Model.incidentNote(snapshot) : null
    readonly property bool canStart: connected && snapshot.state === "idle" && !blocker && !busy
    // The session rows need known glasses (one pair, a known model; a Pro 2 with its bridge).
    readonly property bool sessionKnown: connected && !!snapshot.model && snapshot.model !== "unknown"
                                         && !(snapshot.model === "pro2" && !snapshot.pro2)

    DeviceCard { objectName: "deviceCard"; info: Model.deviceCard(root.snapshot, root.connected, root.settings) }
    BigButton {
        objectName: "startButton"
        text: "Start XR"
        enabled: root.canStart
        onClicked: root.request({op: "begin_start"})
    }
    PanelText {
        visible: root.connected && !!root.snapshot.model
        text: "You confirm what the session does before it starts."
        alpha: 0.65; font.pixelSize: 11
        horizontalAlignment: Text.AlignHCenter
    }

    // The service is not running: the one thing to do is start it.
    Note {
        objectName: "serviceNote"
        visible: !root.connected
        tone: root.serviceError ? "err" : "warn"
        title: root.serviceError ? "The service did not start." : "Start the background service to continue."
        text: root.serviceError || "It watches for your glasses and runs each session."
    }
    RowLayout {
        visible: !root.connected
        spacing: 12
        Ui.Button {
            objectName: "startService"
            text: root.serviceRunning ? "Starting service…" : root.serviceTried ? "Try again" : "Start service"
            bordered: true
            enabled: !root.serviceRunning
            opacity: enabled ? 1 : 0.5
            onClicked: root.startService()
        }
        PanelText { text: "systemctl --user restart viture-workspace-controller"; alpha: 0.65; font.pixelSize: 11 }
    }

    Note {
        objectName: "blockerNote"
        visible: !!root.blocker
        tone: root.blocker ? root.blocker.tone : "warn"
        title: root.blocker ? root.blocker.title : ""
        text: root.blocker ? root.blocker.text : ""
        Ui.Dropdown {
            objectName: "fallbackChoice"
            visible: !!root.blocker && root.blocker.kind === "choose"
            showLabel: false
            value: "Choose monitor"
            options: root.snapshot.fallback_candidates || []
            foreground: Tone.warn
            implicitWidth: 170
            enabled: !root.busy
            onChanged: value => root.request({op: "set_settings", settings: {fallback: value}})
        }
    }
    CodeBox { visible: !!root.blocker && root.blocker.kind === "pro2-bridge"; text: "docs/pro2/bring-up-runbook.md" }
    Note {
        objectName: "errorNote"
        visible: root.error !== ""
        tone: "err"
        title: "That did not work"
        text: Model.reasonText(root.error) ? [Model.reasonText(root.error).title, Model.reasonText(root.error).text].filter(t => t).join(" ") : ""
        dismissible: true
        onDismissed: root.dismissError()
    }
    Note {
        objectName: "incidentNote"
        visible: !!root.incident
        tone: "quiet"
        title: root.incident ? root.incident.title : ""
        text: root.incident ? root.incident.text : ""
    }

    Item { Layout.fillHeight: true; Layout.minimumHeight: 6 }

    RowLayout {
        visible: root.sessionKnown
        SectionLabel { text: "This session"; Layout.fillWidth: true }
        Ui.Button {
            objectName: "sessionSetup"
            text: "Session setup  ›"
            bordered: true
            onClicked: root.openSetup()
        }
    }
    InfoRows {
        objectName: "sessionRows"
        visible: root.sessionKnown
        rows: root.sessionKnown ? Model.sessionRows(root.snapshot, root.settings) : []
        onClicked: root.openSetup()
    }

    footer: PanelText {
        objectName: "footerText"
        alpha: 0.55; font.pixelSize: 11
        text: !root.connected ? "Still failing? journalctl --user -u viture-workspace-controller"
            : !root.snapshot.model ? "Supported: Luma Ultra, Pro 2, Beast."
            : root.snapshot.model === "pro2" && !root.snapshot.pro2 ? "Head tracking: Pro 2 SDK bridge · not installed"
            : "Everything else is set in the glasses. Ctrl+Alt+H there lists the keys."
    }
}
