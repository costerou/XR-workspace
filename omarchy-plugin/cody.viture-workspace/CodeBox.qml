import QtQuick
import QtQuick.Layouts
import qs.Commons

// A command or path to read or select: never wrapped mid-word unless it has to be.
Rectangle {
    id: root
    property string text: ""
    Layout.fillWidth: true
    implicitHeight: edit.implicitHeight + 20
    color: Tone.a(0.04)
    border.width: 1
    border.color: Tone.a(0.18)
    TextEdit {
        id: edit
        objectName: "codeText"
        x: 10; y: 10; width: parent.width - 20
        text: root.text
        readOnly: true
        selectByMouse: true
        wrapMode: TextEdit.WrapAtWordBoundaryOrAnywhere
        textFormat: TextEdit.PlainText
        color: Tone.a(0.65)
        selectionColor: Tone.a(0.25)
        font.family: Style.font.family
        font.pixelSize: 11
    }
}
