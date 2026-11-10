import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import qs.Commons

// A page body that scrolls, at least as tall as the window (so an `Item { Layout.fillHeight: true }` pushes what follows to
// the bottom), over a fixed footer that never scrolls away.
ColumnLayout {
    id: root
    default property alias content: body.data
    property alias footer: footerRow.data
    property bool footerVisible: true
    property alias bodySpacing: body.spacing
    spacing: 0
    Flickable {
        id: flick
        Layout.fillWidth: true
        Layout.fillHeight: true
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        contentWidth: width
        contentHeight: body.height + 36
        ScrollBar.vertical: ScrollBar { policy: flick.contentHeight > flick.height ? ScrollBar.AsNeeded : ScrollBar.AlwaysOff }
        ColumnLayout {
            id: body
            x: 18; y: 18
            width: flick.width - 36
            height: Math.max(implicitHeight, flick.height - 36)
            spacing: 14
        }
    }
    Rectangle { visible: root.footerVisible; Layout.fillWidth: true; Layout.preferredHeight: 1; color: Tone.a(0.12) }
    Item {
        visible: root.footerVisible
        Layout.fillWidth: true
        Layout.preferredHeight: Math.max(42, footerRow.implicitHeight + 20)
        RowLayout {
            id: footerRow
            anchors.fill: parent
            anchors.leftMargin: 18; anchors.rightMargin: 18; anchors.topMargin: 10; anchors.bottomMargin: 10
            spacing: 10
        }
    }
}
