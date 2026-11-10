import QtQuick
import QtQuick.Layouts
import qs.Commons

// The page's one large action (Start XR): accent fill when it can run, a dim outline when it cannot.
Rectangle {
    id: root
    property string text: ""
    signal clicked()
    Layout.fillWidth: true
    implicitHeight: 64
    activeFocusOnTab: enabled
    color: !enabled ? Tone.a(0.04)
         : mouse.pressed ? Qt.darker(Tone.accent, 1.15)
         : mouse.containsMouse ? Qt.lighter(Tone.accent, 1.08) : Tone.accent
    border.width: !enabled || activeFocus ? 1 : 0
    border.color: activeFocus ? Tone.fg : Tone.a(0.18)
    Accessible.role: Accessible.Button
    Accessible.name: text
    Keys.onReturnPressed: root.clicked()
    Keys.onSpacePressed: root.clicked()
    Text {
        anchors.centerIn: parent
        text: root.text
        color: root.enabled ? Tone.bg : Tone.a(0.40)
        font.family: Style.font.family
        font.pixelSize: 24
        font.weight: Font.ExtraBold
        font.letterSpacing: 1
    }
    MouseArea {
        id: mouse
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: root.clicked()
    }
}
