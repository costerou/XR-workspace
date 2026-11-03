import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// The project logo (assets/logo.svg) in the panel header: its emblem (logo-emblem.svg, a copy of the logo's emblem
// group) beside the "XR / Workspace" wordmark. The wordmark is text in the theme's font and colours rather than the
// SVG's, so it reads on light and dark Omarchy themes alike.
RowLayout {
    id: root
    readonly property string name: "XR Workspace"
    property int size: 44
    spacing: Math.round(size * 0.3)
    Accessible.role: Accessible.Graphic
    Accessible.name: name
    Image {
        objectName: "logoEmblem"
        source: Qt.resolvedUrl("logo-emblem.svg")
        Layout.preferredWidth: root.size
        Layout.preferredHeight: root.size
        sourceSize.width: root.size * 2
        sourceSize.height: root.size * 2
        fillMode: Image.PreserveAspectFit
        smooth: true
    }
    ColumnLayout {
        spacing: 0
        Label {
            objectName: "logoTitle"
            text: "XR"
            font.pixelSize: Math.round(root.size * 0.46)
            font.weight: Font.ExtraBold
            font.letterSpacing: Math.max(1, root.size / 30)
        }
        Label {
            objectName: "logoSubtitle"
            text: "Workspace"
            font.pixelSize: Math.round(root.size * 0.33)
            opacity: 0.72
        }
    }
}
