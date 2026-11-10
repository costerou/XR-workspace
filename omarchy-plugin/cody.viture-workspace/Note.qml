import QtQuick
import QtQuick.Layouts
import qs.Commons

// The one block under a control that says what is wrong and what to do: warn (amber), err (red) or quiet (history, hints).
// Extra controls (a monitor choice) go on its right; a dismissible note has a close mark.
Rectangle {
    id: root
    property string tone: "warn"
    property string title: ""
    property string text: ""
    property bool dismissible: false
    default property alias actions: actionRow.data
    signal dismissed()
    readonly property color ink: tone === "quiet" ? Tone.fg : Tone.of(tone)
    Layout.fillWidth: true
    implicitHeight: row.implicitHeight + 20
    color: tone === "quiet" ? Tone.a(0.04) : Tone.tint(ink, tone === "err" ? 0.10 : 0.09)
    Rectangle { width: 2; height: parent.height; color: root.tone === "quiet" ? Tone.a(0.40) : root.ink }
    RowLayout {
        id: row
        anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
        anchors.leftMargin: 14; anchors.rightMargin: 10
        spacing: 10
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 2
            PanelText { objectName: "noteTitle"; visible: text !== ""; text: root.title; color: root.ink; font.bold: true }
            PanelText { objectName: "noteText"; visible: text !== ""; text: root.text; color: root.tone === "quiet" ? Tone.a(0.75) : root.ink }
        }
        RowLayout { id: actionRow; spacing: 8 }
        Text {
            objectName: "dismissNote"
            visible: root.dismissible
            text: "✕"
            color: root.ink
            font.pixelSize: 12
            Layout.alignment: Qt.AlignTop
            MouseArea { anchors.fill: parent; anchors.margins: -6; cursorShape: Qt.PointingHandCursor; onClicked: root.dismissed() }
        }
    }
}
