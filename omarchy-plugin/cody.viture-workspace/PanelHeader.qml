import QtQuick
import QtQuick.Layouts
import qs.Commons
import qs.Ui as Ui

// The panel's top bar: the mark and "XR Workspace", or "‹ Back" and the page's title on a sub-page, and the close button.
// Shared by every page, the running page included.
Item {
    id: root
    property string title: "XR Workspace"
    property bool backVisible: false
    signal back()
    signal close()
    implicitHeight: 44
    Rectangle { anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; height: 1; color: Tone.a(0.12) }
    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: root.backVisible ? 8 : 18
        anchors.rightMargin: 8
        spacing: 10
        Ui.Button {
            objectName: "backButton"
            visible: root.backVisible
            text: "‹ Back"
            fontSize: 11
            foreground: Tone.a(0.65)
            onClicked: root.back()
        }
        Image {
            visible: !root.backVisible
            source: Qt.resolvedUrl("logo-emblem.svg")
            Layout.preferredWidth: 22; Layout.preferredHeight: 22
            sourceSize.width: 44; sourceSize.height: 44
            fillMode: Image.PreserveAspectFit
            smooth: true
        }
        Text {
            objectName: "pageTitle"
            text: root.title
            color: Tone.fg
            font.family: Style.font.family
            font.pixelSize: 13
            font.bold: true
            textFormat: Text.PlainText
        }
        Item { Layout.fillWidth: true }
        Ui.Button {
            objectName: "closeButton"
            text: "✕"
            foreground: Tone.a(0.65)
            onClicked: root.close()
        }
    }
}
