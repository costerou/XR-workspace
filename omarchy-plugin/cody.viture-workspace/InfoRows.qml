import QtQuick
import QtQuick.Layouts
import qs.Commons

// Read-only key / value rows in a hairline box (Model.sessionRows): {key, value, warn}.
Rectangle {
    id: root
    property var rows: []
    signal clicked()
    Layout.fillWidth: true
    implicitHeight: column.implicitHeight + 2
    color: "transparent"
    border.width: 1
    border.color: Tone.a(0.12)
    Column {
        id: column
        x: 1; y: 1; width: parent.width - 2
        Repeater {
            model: root.rows
            delegate: Item {
                id: entry
                required property var modelData
                required property int index
                width: column.width
                height: 34
                Rectangle { visible: entry.index > 0; width: parent.width; height: 1; color: Tone.a(0.12) }
                PanelText { anchors.left: parent.left; anchors.leftMargin: 14; anchors.verticalCenter: parent.verticalCenter; width: implicitWidth
                            text: entry.modelData.key; alpha: 0.65; wrapMode: Text.NoWrap }
                PanelText { objectName: "row:" + entry.modelData.key
                            anchors.right: parent.right; anchors.rightMargin: 14; anchors.verticalCenter: parent.verticalCenter
                            width: Math.min(implicitWidth, parent.width * 0.62); horizontalAlignment: Text.AlignRight
                            text: entry.modelData.value; tone: entry.modelData.warn ? "warn" : "fg"; wrapMode: Text.NoWrap; elide: Text.ElideLeft }
            }
        }
    }
    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: root.clicked() }
}
