pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell.Io

// VR video in a running session (docs/vr-video.md). A thin client like the rest of the panel: the run itself checks,
// probes and opens the file and owns the playback state; this shows it and sends paths and key operations.
ColumnLayout {
    id: root
    required property var snapshot
    property bool busy: false
    // The file picker; overridable for offline tests.
    property var pickerCommand: ["zenity", "--file-selection", "--title=Play a VR video (180 / 360)",
        "--file-filter=Videos | *.mp4 *.MP4 *.mkv *.MKV *.webm *.mov *.MOV *.m4v *.avi *.ts *.m2ts", "--file-filter=All files | *"]
    readonly property var video: snapshot.video || ({})
    readonly property bool playing: !!video.active
    // Panel buttons for the video keys (tools/native_spike/video.py KEY_OPS) and the run's recenter.
    readonly property var controls: [["video_pause", "Play / pause"], ["video_seek_back_big", "−60 s"], ["video_seek_back", "−10 s"],
        ["video_seek_fwd", "+10 s"], ["video_seek_fwd_big", "+60 s"], ["video_format", "Next format"], ["video_controls", "Show / hide controls"],
        ["recenter", "Recenter"], ["video_exit", "Back to desktops"]]
    signal request(var message)
    spacing: 8

    function play(path) {
        const p = String(path || "").trim();
        if (p.startsWith("/")) root.request({op: "video_open", path: p});
    }
    function control(op) { root.request({op: "view_op", operation: op}); }
    function browse() { if (!picker.running) picker.running = true; }

    Label { text: "VR video" + (root.playing ? " · " + (root.video.state || "loading") : ""); font.bold: true }
    RowLayout {
        visible: !root.playing
        Layout.fillWidth: true
        TextField { id: pathField; objectName: "videoPath"; Layout.fillWidth: true; placeholderText: "/home/you/Videos/clip_360_TB.mp4"; onAccepted: root.play(text) }
        Button { objectName: "videoBrowse"; text: "Browse…"; enabled: !root.busy && !picker.running; onClicked: root.browse() }
        Button { objectName: "videoPlay"; text: "Play"; enabled: !root.busy && pathField.text.trim().startsWith("/"); onClicked: root.play(pathField.text) }
    }
    Label {
        visible: !root.playing; Layout.fillWidth: true; wrapMode: Text.WordWrap; opacity: 0.65
        text: "Or press Ctrl+Alt+O in the glasses. 360° or 180°, mono or 3D (side-by-side / top-bottom), equirectangular or fisheye. The desktops come back when you exit (Esc)."
    }
    Label {
        visible: root.playing; Layout.fillWidth: true; elide: Text.ElideRight
        text: (root.video.title || "") + " · " + (root.video.label || "") + (root.video.source === "chosen" ? "" : " (detected from " + (root.video.source || "?") + ")")
    }
    Flow {
        visible: root.playing; Layout.fillWidth: true; Layout.preferredHeight: childrenRect.height; spacing: 8
        Repeater {
            model: root.controls
            delegate: Button {
                required property var modelData
                text: modelData[1]; enabled: !root.busy
                onClicked: root.control(modelData[0])
            }
        }
    }
    Label {
        visible: root.playing && (root.video.warnings || []).length > 0; Layout.fillWidth: true; wrapMode: Text.WordWrap; opacity: 0.65
        text: (root.video.warnings || []).join(" · ")
    }
    Label { objectName: "videoError"; visible: !!root.video.error; Layout.fillWidth: true; wrapMode: Text.WordWrap; color: "#e06c75"; text: root.video.error || "" }
    Process {
        id: picker
        command: root.pickerCommand
        stdout: StdioCollector {
            onStreamFinished: {
                const p = this.text.trim();
                if (p) { pathField.text = p; root.play(p); }
            }
        }
    }
}
