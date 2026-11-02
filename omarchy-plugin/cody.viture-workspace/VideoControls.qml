pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell.Io

// VR video in a running session (docs/vr-video.md). A thin client like the rest of the panel: the run itself checks,
// probes and opens the file and owns the playback state; this shows it and sends paths and key operations.
// Web videos: an http(s) video or page link typed here, or "Paste from browser" (the clipboard once). The run
// validates it, resolves a page link with yt-dlp and relays the stream.
ColumnLayout {
    id: root
    required property var snapshot
    property bool busy: false
    // The file picker; overridable for offline tests.
    property var pickerCommand: ["zenity", "--file-selection", "--title=Play a VR video (180 / 360)",
        "--file-filter=Videos | *.mp4 *.MP4 *.mkv *.MKV *.webm *.mov *.MOV *.m4v *.avi *.ts *.m2ts", "--file-filter=All files | *"]
    // Reads the clipboard once when "Paste from browser" is pressed; overridable for offline tests.
    property var pasteCommand: ["wl-paste", "--no-newline", "--type", "text"]
    property string pasteError: ""
    readonly property var stream: video.stream || null
    readonly property var video: snapshot.video || ({})
    readonly property bool playing: !!video.active
    // Panel buttons for the video keys (tools/native_spike/video.py KEY_OPS) and the run's recenter.
    readonly property var controls: [["video_pause", "Play / pause"], ["video_seek_back_big", "−60 s"], ["video_seek_back", "−10 s"],
        ["video_seek_fwd", "+10 s"], ["video_seek_fwd_big", "+60 s"], ["video_format", "Next format"], ["video_controls", "Show / hide controls"],
        ["recenter", "Recenter"], ["video_exit", "Back to desktops"]]
    signal request(var message)
    spacing: 8

    function isLink(text) { return /^https?:\/\/\S+$/i.test(String(text || "").trim()); }
    function play(path) {
        const p = String(path || "").trim();
        if (p.startsWith("/")) root.request({op: "video_open", path: p});
        else if (root.isLink(p)) root.request({op: "video_open", url: p});
    }
    // Clipboard text -> the same request as Ctrl+Alt+U (tools/native_spike/xr_control.py clipboard_payload).
    function playClipboard(text) {
        const t = String(text || "").trim();
        root.pasteError = "";
        if (t.length > 16384) { root.pasteError = "The clipboard holds too much text to be a video link."; return; }
        if (root.isLink(t)) { root.request({op: "video_open", url: t}); return; }
        root.pasteError = t ? "The clipboard holds no video link. Copy the video page or video link first."
                            : "The clipboard is empty. Copy a video link first.";
    }
    function paste() { if (!paster.running) paster.running = true; }
    function clock(s) {
        if (s === null || s === undefined) return "--:--";
        const t = Math.max(0, Math.floor(s)), h = Math.floor(t / 3600), m = Math.floor(t / 60) % 60, sec = t % 60;
        return (h ? h + ":" + String(m).padStart(2, "0") : String(m)) + ":" + String(sec).padStart(2, "0");
    }
    function control(op) { root.request({op: "view_op", operation: op}); }
    function browse() { if (!picker.running) picker.running = true; }

    Label { text: "VR video" + (root.playing ? " · " + (root.video.state || "loading") : ""); font.bold: true }
    RowLayout {
        visible: !root.playing
        Layout.fillWidth: true
        TextField { id: pathField; objectName: "videoPath"; Layout.fillWidth: true; placeholderText: "/home/you/Videos/clip_360_TB.mp4 or https://…"; onAccepted: root.play(text) }
        Button { objectName: "videoBrowse"; text: "Browse…"; enabled: !root.busy && !picker.running; onClicked: root.browse() }
        Button { objectName: "videoPlay"; text: "Play"; enabled: !root.busy && (pathField.text.trim().startsWith("/") || root.isLink(pathField.text)); onClicked: root.play(pathField.text) }
    }
    RowLayout {
        visible: !root.playing
        Layout.fillWidth: true
        Button { objectName: "videoPaste"; text: "Paste from browser"; enabled: !root.busy && !paster.running && !root.video.opening; onClicked: root.paste() }
        Label { visible: !!root.video.opening; text: "Opening the stream…"; opacity: 0.65 }
    }
    Label {
        visible: !root.playing; Layout.fillWidth: true; wrapMode: Text.WordWrap; opacity: 0.65
        text: "Or press Ctrl+Alt+O in the glasses for a file, Ctrl+Alt+U for a video or video page link you copied. 360° or 180°, mono or 3D (side-by-side / top-bottom), equirectangular or fisheye. The desktops come back when you exit (Esc)."
    }
    Label { objectName: "videoPasteError"; visible: !!root.pasteError && !root.playing; Layout.fillWidth: true; wrapMode: Text.WordWrap; color: "#e06c75"; text: root.pasteError }
    Label {
        visible: root.playing; Layout.fillWidth: true; elide: Text.ElideRight
        text: (root.video.title || "") + " · " + (root.video.label || "") + (root.video.source === "chosen" ? "" : root.video.source === "player" ? " (from the web player)" : " (detected from " + (root.video.source || "?") + ")")
    }
    Label {
        objectName: "videoStream"
        visible: root.playing && !!root.stream; Layout.fillWidth: true; elide: Text.ElideRight; opacity: 0.65
        text: root.stream ? "Streaming from " + (root.stream.site || "?") + " (" + (root.stream.kind || "") + ") · " + root.clock(root.stream.position) + " / " + root.clock(root.stream.duration) + (root.stream.seeking ? " · seeking…" : "") : ""
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
        id: paster
        command: root.pasteCommand
        stdout: StdioCollector { onStreamFinished: root.playClipboard(this.text) }
    }
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
