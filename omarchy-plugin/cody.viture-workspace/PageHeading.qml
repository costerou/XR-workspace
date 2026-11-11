import QtQuick
import QtQuick.Layouts

// A status page's large heading and the one line under it.
ColumnLayout {
    property alias title: main.text
    property alias text: sub.text
    Layout.fillWidth: true
    spacing: Tone.space1
    PanelText { id: main; objectName: "heading"; role: "heading" }
    PanelText { id: sub; visible: text !== ""; alpha: Tone.secondary; role: "small" }
}
