pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Commons as Commons
import "Model.js" as Model

Item {
    id: root
    property alias renderSurface: captureSurface
    property var shell: null
    property var manifest: null
    property bool opened: false
    // Injection is for offline rendering only; production uses the passive socket client.
    property alias socketPath: client.socketPath
    property int selectedDesktop: -1
    property var settings: ({})
    property var pending: null
    property string error: ""
    property int commandId: 0
    property var queue: []
    property int settingsId: 0
    readonly property var snapshot: client.snapshot
    readonly property bool busy: commandId !== 0
    readonly property bool prestart: client.connected && snapshot.state === "idle"
    function open(payload) { if (payload && Number.isInteger(payload.desktop)) selectedDesktop=payload.desktop; error=""; opened=true; client.send({op:"status"}); loadSettings() }
    function close() { opened=false }
    function dismiss() { if (shell && typeof shell.hide === "function") shell.hide((manifest && manifest.id) || "cody.viture-workspace"); else close() }
    function loadSettings() { if (client.connected) settingsId=client.send({op:"get_settings"}) }
    function request(message) {
        if (busy) return;
        error="";
        const messages=Array.isArray(message) ? message : [message];
        if (!messages.length) return;
        queue=messages.slice(1);
        commandId=client.send(messages[0]);
    }
    Process {
        id: controllerService
        command: ["systemctl", "--user", "restart", "viture-workspace-controller.service"]
        stderr: StdioCollector {
            onStreamFinished: {
                if (text.trim()) root.error = text.trim().slice(0, 2048)
            }
        }
        onExited: function(exitCode, exitStatus) {
            if (exitCode !== 0 || exitStatus !== 0) {
                if (!root.error) root.error = "Could not start controller. Check the user service logs."
            }
        }
    }
    ControllerClient {
        id:client
        onStateChanged: {
            root.error=""
            if (snapshot.state !== "confirming") root.pending=null
            else if (snapshot.pending) root.pending=snapshot.pending
            if (root.opened) root.loadSettings()
        }
        onReplied: function(id, reply) {
            if (id===root.settingsId) { if (reply.ok) root.settings=reply.settings; return }
            if (id!==root.commandId) return
            root.commandId=0
            if (!reply.ok) { root.queue=[]; root.error=reply.error || "Request refused"; return }
            if (root.queue.length) { const remaining=root.queue; root.queue=remaining.slice(1); root.commandId=client.send(remaining[0]); return }
            if (reply.scope) root.pending={action:reply.action,scope:reply.scope,scope_id:reply.scope_id}
            root.loadSettings()
            client.send({op:"status"})
        }
    }
    FloatingWindow {
        id:window
        visible:root.opened
        // Also the desktop entry's StartupWMClass and adopt.py PANEL_TITLE: keep the three equal.
        title:"XR Workspace"
        implicitWidth:700; implicitHeight:860
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
        Flickable {
            anchors.fill:parent; anchors.margins:20; clip:true
            boundsBehavior:Flickable.StopAtBounds
            contentWidth:width
            ScrollBar.vertical:ScrollBar {}
            contentHeight:body.implicitHeight
            ColumnLayout {
                id:body
                width:parent.width
                height:implicitHeight
                spacing:14
                RowLayout {
                    // The pre-start page (Launcher) shows the logo large under this row; every other page has it here.
                    Logo { size:48; visible:!root.prestart }
                    Item { Layout.fillWidth:true }
                    Button { text:"Close"; onClicked:root.dismiss() }
                }
                Label { text:root.error || (client.connected ? root.snapshot.reason || "" : "controller not running"); textFormat:Text.PlainText; visible:text!==""; Layout.fillWidth:true; wrapMode:Text.WordWrap; color:"#e7c78a" }
                Label { text:Model.incidentLine(root.snapshot); textFormat:Text.PlainText; visible:text!==""; Layout.fillWidth:true; wrapMode:Text.WordWrap; color:"#e7c78a" }
                Label { objectName:"trackingInUse"; text:Model.trackingInUseLine(root.snapshot.tracking_in_use); textFormat:Text.PlainText; visible:text!==""; Layout.fillWidth:true; wrapMode:Text.WordWrap; horizontalAlignment:Text.AlignHCenter; opacity:Model.trackingInUseWarns(root.snapshot.tracking_in_use) ? 1 : 0.65; color:Model.trackingInUseWarns(root.snapshot.tracking_in_use) ? "#e7c78a" : palette.windowText }
                Button {
                    visible:!client.connected || controllerService.running
                    text:controllerService.running ? "Starting controller…" : "Start / restart controller"
                    enabled:!controllerService.running
                    onClicked: {
                        root.error = ""
                        controllerService.running = true
                    }
                }
                Loader {
                    Layout.fillWidth:true
                    Layout.preferredHeight:item ? (item as Item).implicitHeight : 0
                    active:client.connected
                    sourceComponent: {
                        switch(root.snapshot.state) {
                        case "idle": return launcher
                        case "running": return running
                        case "confirming": return confirmation
                        default: return notice
                        }
                    }
                }
            }
        }
        }
        Shortcut { sequence:"Escape"; onActivated:root.dismiss() }
    }
    Component { id:launcher; Launcher { selected:root.selectedDesktop; onSelectedChanged:root.selectedDesktop=selected; snapshot:root.snapshot; settings:root.settings; busy:root.busy; onRequest:message => root.request(message) } }
    Component { id:running; RunningEditor { snapshot:root.snapshot; settings:root.settings; busy:root.busy; onRequest:message => root.request(message) } }
    Component {
        id:confirmation
        ColumnLayout {
            Label { visible:!root.pending; text:"Confirmation scope unavailable. Cancel and begin again."; wrapMode:Text.WordWrap; Layout.fillWidth:true }
            Button { visible:!root.pending; text:"Cancel"; onClicked:root.request({op:"cancel"}) }
            Loader {
                Layout.fillWidth:true; active:!!root.pending
                sourceComponent: ConfirmDialog {
                    pending:root.pending; busy:root.busy
                    onCancel:root.request({op:"cancel"})
                    onConfirm: function(optional) { const m={op:root.pending.action==="recover" ? "confirm_recover" : "confirm_start",scope_id:root.pending.scope_id}; if(root.pending.action==="start") m.optional=optional; root.request(m) }
                }
            }
        }
    }
    Component {
        id:notice
        SessionNotice {
            snapshot: root.snapshot
            busy: root.busy
            onRequest: message => root.request(message)
        }
    }
}
