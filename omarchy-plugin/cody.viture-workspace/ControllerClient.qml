import QtQuick
import Quickshell
import Quickshell.Io

QtObject {
    id: root

    // Overridable for offline socket tests; never launches the controller.
    property string socketPath: Quickshell.env("XDG_RUNTIME_DIR") + "/viture-workspace/controller.sock"
    readonly property bool connected: socket.connected
    property var snapshot: ({})
    readonly property string error: connected ? "" : "controller not running"
    property int nextRequestId: 1
    property var pending: ({})
    signal replied(int id, var reply)
    signal stateChanged()

    // Wire envelope: schema:1, request_id:<integer>, op:<request name>.
    // On disconnect fail pending requests locally; never replay consent or mutations.
    function send(obj: var): int {
        const id = nextRequestId++;
        if (!connected) {
            Qt.callLater(function() { root.replied(id, {ok:false, error:"controller not running"}); });
            return id;
        }
        const frame = Object.assign({}, obj, {schema:1, request_id:id});
        const line = JSON.stringify(frame) + "\n";
        // UTF-8 byte length, matching the controller's 16 KiB wire limit.
        if (unescape(encodeURIComponent(line)).length > 16384) {
            Qt.callLater(function() { root.replied(id, {ok:false, error:"request too large"}); });
            return id;
        }
        pending[id] = obj.op;
        socket.write(line);
        socket.flush();
        return id;
    }

    function receive(line: string): void {
        let msg;
        try { msg = JSON.parse(line); } catch (e) { return; }
        if (!msg || msg.schema !== 1) return;
        if (msg.event === "state") {
            const value = Object.assign({}, msg);
            delete value.event;
            delete value.schema;
            snapshot = value;
            stateChanged();
        } else if (Number.isInteger(msg.request_id) && pending[msg.request_id]) {
            const op = pending[msg.request_id];
            delete pending[msg.request_id];
            // Status replies carry the snapshot fields at the top level.
            if (op === "status" && msg.ok && msg.state !== undefined) {
                const value = Object.assign({}, msg);
                delete value.schema;
                delete value.request_id;
                delete value.ok;
                snapshot = value;
                stateChanged();
            }
            replied(msg.request_id, msg);
        }
    }

    property Socket socket: Socket {
        path: root.socketPath
        connected: true
        parser: SplitParser {
            onRead: data => root.receive(data)
        }
        onConnectionStateChanged: {
            if (connected) {
                // Let the public connected binding update before send() checks it.
                Qt.callLater(function() { if (root.connected) root.send({op:"status"}); });
            } else {
                const ids = Object.keys(root.pending);
                root.pending = ({});
                root.snapshot = ({});
                root.stateChanged();
                for (const id of ids)
                    root.replied(Number(id), {ok:false, error:"controller not running"});
            }
        }
    }
    property Timer reconnect: Timer {
        interval: 2000
        repeat: true
        running: !root.connected
        onTriggered: root.socket.connected = true
    }
}
