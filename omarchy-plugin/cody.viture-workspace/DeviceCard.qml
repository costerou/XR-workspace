import QtQuick
import QtQuick.Layouts
import qs.Commons

// Which glasses, where, and the one-word state (Model.deviceCard): the top of Home, Progress and Recovery.
Rectangle {
    id: root
    required property var info
    Layout.fillWidth: true
    implicitHeight: 64
    color: Tone.a(0.04)
    border.width: 1
    border.color: Tone.a(0.18)
    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 14; anchors.rightMargin: 14
        spacing: 14
        Emblem { Layout.preferredWidth: 40; Layout.preferredHeight: 30; color: root.info.dim ? Tone.a(0.40) : Tone.fg }
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 2
            PanelText { objectName: "deviceTitle"; text: root.info.title; font.pixelSize: 15; font.bold: true; wrapMode: Text.NoWrap; elide: Text.ElideRight }
            PanelText { objectName: "deviceSub"; text: root.info.sub; alpha: 0.65; font.pixelSize: 11; wrapMode: Text.NoWrap; elide: Text.ElideRight }
        }
        Rectangle { Layout.preferredWidth: 6; Layout.preferredHeight: 6; radius: 3; color: Tone.of(root.info.tone) }
        PanelText { objectName: "deviceStatus"; text: root.info.status; font.pixelSize: 11; Layout.fillWidth: false; wrapMode: Text.NoWrap; alpha: 0.85 }
    }
}
