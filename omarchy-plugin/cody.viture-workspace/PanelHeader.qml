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
    Rectangle { anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; height: 1; color: Tone.a(Tone.hairline) }
    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: root.backVisible ? 8 : 18
        anchors.rightMargin: 8
        spacing: 10
        Ui.Button {
            objectName: "backButton"
            visible: root.backVisible
            text: "‹ Back"
            fontSize: Tone.type.small.size
            foreground: Tone.a(Tone.secondary)
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
        PanelText {
            objectName: "pageTitle"
            text: root.title
            role: "strong"
            wrapMode: Text.NoWrap
            Layout.fillWidth: false
        }
        Item { Layout.fillWidth: true }
        Ui.Button {
            objectName: "closeButton"
            text: "✕"
            foreground: Tone.a(Tone.secondary)
            onClicked: root.close()
        }
    }
}
