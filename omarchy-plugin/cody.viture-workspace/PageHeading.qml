import QtQuick
import QtQuick.Layouts

// A status page's large heading and the one line under it.
ColumnLayout {
    property alias title: main.text
    property alias text: sub.text
    Layout.fillWidth: true
    spacing: 4
    PanelText { id: main; objectName: "heading"; font.pixelSize: 22; font.bold: true }
    PanelText { id: sub; visible: text !== ""; alpha: 0.55; font.pixelSize: 11 }
}
