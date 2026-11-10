pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Commons as Commons
import "Model.js" as Model

// The panel window: a fixed 520×660 page under a shared header. Before a session it is Home, Setup (a sub-page of Home),
// the one-run confirmation, the start's progress or a recovery page (docs/mockups/plugin-prestart); during a session it
// is the running editor.
Item {
    id: root
    property alias renderSurface: captureSurface
    property var shell: null
    property var manifest: null
    property bool opened: false
    // Injection is for offline rendering only; production uses the passive socket client.
    property alias socketPath: client.socketPath
    // The desktop Setup has open (-1 for none). Opening the panel for a desktop (open({desktop:n})) lands on Setup with it.
    property int selectedDesktop: -1
    property bool setupOpen: false
    property var settings: ({})
    property var pending: null
    property string error: ""
    property string serviceError: ""
    property bool serviceTried: false
    property int commandId: 0
    property var queue: []
    property int settingsId: 0
    property bool settingsRefreshPending: false
    readonly property var snapshot: client.snapshot
    readonly property bool busy: commandId !== 0
    readonly property string state: client.connected ? snapshot.state || "" : "offline"
    readonly property bool prestart: client.connected && (state === "idle" || state === "no-glasses")
    // Which page shows: one name per page, so the entrance animation plays once per page and not per update.
    readonly property string pageName: !client.connected ? "home"
        : prestart ? (setupOpen && !!snapshot.model && snapshot.model !== "unknown" ? "setup" : "home")
        : state === "confirming" ? "confirm"
        : ["starting", "stopping", "failed"].indexOf(state) >= 0 ? "progress"
        : ["recovery-needed", "cli-recovery", "external-run"].indexOf(state) >= 0 ? "recovery"
        : state === "running" ? "running" : "home"
    function open(payload) {
        const desktop = payload && Number.isInteger(payload.desktop) ? payload.desktop : -1
        selectedDesktop = desktop
        setupOpen = desktop >= 0
        error = ""; opened = true; client.send({op:"status"})
    }
    function close() { opened=false }
    function dismiss() { if (shell && typeof shell.hide === "function") shell.hide((manifest && manifest.id) || "cody.viture-workspace"); else close() }
    function back() {
        if (pageName === "confirm") request({op:"cancel"})
        else { setupOpen = false; selectedDesktop = -1 }
    }
    function startService() { error = ""; serviceError = ""; serviceTried = true; controllerService.running = true }
    function loadSettings() {
        if (!client.connected) return;
        // Keep one read in flight and one refresh after it. A slow controller
        // must not accumulate requests or apply an older reply over a new one.
        if (settingsId !== 0) { settingsRefreshPending=true; return }
        settingsId=client.send({op:"get_settings"});
    }
    function request(message) {
        if (busy) return;
        error="";
        const messages=Array.isArray(message) ? message : [message];
        if (!messages.length) return;
        queue=messages.slice(1);
        commandId=client.send(messages[0]);
    }
    onSelectedDesktopChanged: if (selectedDesktop >= 0) setupOpen = true
    Process {
        id: controllerService
        command: ["systemctl", "--user", "restart", "viture-workspace-controller.service"]
        stderr: StdioCollector {
            onStreamFinished: {
                if (text.trim()) root.serviceError = text.trim().slice(0, 2048)
            }
        }
        onExited: function(exitCode, exitStatus) {
            if (exitCode !== 0 || exitStatus !== 0) {
                if (!root.serviceError) root.serviceError = "systemctl could not start viture-workspace-controller.service."
            }
        }
    }
    ControllerClient {
        id:client
        onStateChanged: {
            if (snapshot.state !== "confirming") root.pending=null
            else if (snapshot.pending) root.pending=snapshot.pending
            if (root.opened) root.loadSettings()
        }
        onConnectedChanged: if (connected) root.serviceError = ""
        onReplied: function(id, reply) {
            if (id===root.settingsId) {
                root.settingsId=0
                // Identical telemetry must not reset an editor's unsaved draft
                // or rebuild every settings binding once per controller tick.
                if (reply.ok && JSON.stringify(root.settings)!==JSON.stringify(reply.settings))
                    root.settings=reply.settings
                if (root.settingsRefreshPending) {
                    root.settingsRefreshPending=false
                    if (root.opened) root.loadSettings()
                }
                return
            }
            if (id!==root.commandId) return
            root.commandId=0
            if (!reply.ok) { root.queue=[]; root.error=reply.error || "Request refused"; return }
            if (root.queue.length) { const remaining=root.queue; root.queue=remaining.slice(1); root.commandId=client.send(remaining[0]); return }
            if (reply.scope) root.pending={action:reply.action,scope:reply.scope,scope_id:reply.scope_id}
            root.loadSettings()
            // The controller pushes changed state after replying to a command.
        }
    }
    FloatingWindow {
        id:window
        visible:root.opened
        // Also the desktop entry's StartupWMClass and adopt.py PANEL_TITLE: keep the three equal.
        title:"XR Workspace"
        implicitWidth:520; implicitHeight:660
        minimumSize: Qt.size(520, 660)
        maximumSize: Qt.size(520, 660)
        color:Commons.Color.background
        onClosed:root.dismiss()
        Pane {
            id:captureSurface
            anchors.fill:parent
            padding:0
            palette.window:Commons.Color.background
            palette.windowText:Commons.Color.foreground
            palette.text:Commons.Color.foreground
            palette.buttonText:Commons.Color.foreground
            palette.button:Qt.lighter(Commons.Color.background,1.6)
            palette.disabled.windowText:Commons.Color.muted
            palette.disabled.text:Commons.Color.muted
            palette.disabled.buttonText:Commons.Color.muted
            palette.base:Qt.darker(Commons.Color.background,1.2)
            palette.highlight:Commons.Color.accent
            palette.highlightedText:Commons.Color.background
            background:Rectangle { color:Commons.Color.background }
            ColumnLayout {
                anchors.fill: parent
                spacing: 0
                PanelHeader {
                    Layout.fillWidth: true
                    backVisible: root.pageName === "setup" || root.pageName === "confirm"
                    title: root.pageName === "setup" ? "Session setup" : root.pageName === "confirm" ? "Confirm" : "XR Workspace"
                    onBack: root.back()
                    onClose: root.dismiss()
                }
                // A refused request on a page without its own place for it (Home shows it under Start).
                Note {
                    objectName: "requestError"
                    visible: root.error !== "" && root.pageName !== "home"
                    Layout.leftMargin: 18; Layout.rightMargin: 18; Layout.topMargin: 12
                    tone: "err"
                    title: "That did not work"
                    text: Model.reasonText(root.error) ? [Model.reasonText(root.error).title, Model.reasonText(root.error).text].filter(t => t).join(" ") : ""
                    dismissible: true
                    onDismissed: root.error = ""
                }
                Loader {
                    id: page
                    objectName: "panelPage"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    property string shown: root.pageName
                    sourceComponent: ({home: home, setup: setup, confirm: confirmation, progress: progress, recovery: recovery, running: running})[shown]
                    // A page change animates once; telemetry updates within that page do not replay it.
                    onLoaded: pageEntrance.restart()
                    ParallelAnimation {
                        id: pageEntrance
                        NumberAnimation { target: page; property: "opacity"; from: 0; to: 1; duration: Motion.enterDuration; easing.type: Easing.OutCubic }
                        NumberAnimation { target: page; property: "scale"; from: 0.97; to: 1; duration: Motion.enterDuration; easing.type: Easing.OutCubic }
                    }
                }
            }
        }
        Shortcut { sequence:"Escape"; onActivated:root.dismiss() }
    }
    Component {
        id: home
        HomePage {
            snapshot: root.snapshot; settings: root.settings; connected: client.connected; busy: root.busy
            error: root.error; serviceRunning: controllerService.running; serviceError: root.serviceError; serviceTried: root.serviceTried
            onRequest: message => root.request(message)
            onOpenSetup: root.setupOpen = true
            onStartService: root.startService()
            onDismissError: root.error = ""
        }
    }
    Component {
        id: setup
        SetupPage {
            snapshot: root.snapshot; settings: root.settings; busy: root.busy; selected: root.selectedDesktop
            onSelect: index => root.selectedDesktop = index
            onRequest: message => root.request(message)
        }
    }
    Component { id: confirmation; ConfirmPage { pending: root.pending; busy: root.busy; onRequest: message => root.request(message) } }
    Component { id: progress; ProgressPage { snapshot: root.snapshot; busy: root.busy; onRequest: message => root.request(message) } }
    Component { id: recovery; RecoveryPage { snapshot: root.snapshot; busy: root.busy; onRequest: message => root.request(message) } }
    // The running session's page is unchanged: the editor, under the driver in use and the controller's reason.
    Component {
        id: running
        Flickable {
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            contentWidth: width
            contentHeight: runningBody.implicitHeight + 36
            ScrollBar.vertical: ScrollBar {}
            ColumnLayout {
                id: runningBody
                x: 18; y: 18; width: parent.width - 36
                spacing: 14
                Label { text: root.snapshot.reason || ""; textFormat: Text.PlainText; visible: text !== ""; Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Tone.warn }
                Label { objectName:"trackingInUse"; text:Model.trackingInUseLine(root.snapshot.tracking_in_use); textFormat:Text.PlainText; visible:text!==""; Layout.fillWidth:true; wrapMode:Text.WordWrap; horizontalAlignment:Text.AlignHCenter; opacity:Model.trackingInUseWarns(root.snapshot.tracking_in_use) ? 1 : 0.65; color:Model.trackingInUseWarns(root.snapshot.tracking_in_use) ? Tone.warn : palette.windowText }
                RunningEditor { Layout.fillWidth: true; snapshot: root.snapshot; settings: root.settings; busy: root.busy; onRequest: message => root.request(message) }
            }
        }
    }
}
