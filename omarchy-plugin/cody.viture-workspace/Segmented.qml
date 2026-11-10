import QtQuick
import QtQuick.Layouts
import qs.Commons

// A row of square hairline segments: {value, label, disabled}. A disabled one is struck through and cannot be picked.
Row {
    id: root
    property var options: []
    property var value
    signal picked(var value)
    Repeater {
        model: root.options
        delegate: Rectangle {
            id: cell
            required property var modelData
            required property int index
            readonly property bool chosen: modelData.value === root.value
            objectName: "segment:" + modelData.label
            enabled: !modelData.disabled
            width: Math.max(38, label.implicitWidth + 20)
            height: 28
            color: chosen ? Tone.a(0.18) : mouse.containsMouse && enabled ? Tone.a(0.08) : "transparent"
            border.width: 1
            border.color: Tone.a(0.18)
            Text {
                id: label
                anchors.centerIn: parent
                text: cell.modelData.label
                color: cell.enabled ? Tone.fg : Tone.a(0.40)
                font.family: Style.font.family
                font.pixelSize: 12
                font.bold: cell.chosen
                font.strikeout: !cell.enabled
            }
            MouseArea { id: mouse; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: root.picked(cell.modelData.value) }
        }
    }
}
