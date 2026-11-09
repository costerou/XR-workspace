import QtQuick
import QtQuick.Layouts

Item {
    id: root
    property bool shown: false
    default property alias contentData: content.data
    property alias spacing: content.spacing
    property real progress: shown ? 1 : 0
    implicitHeight: content.implicitHeight * progress
    implicitWidth: content.implicitWidth
    visible: shown || progress > 0
    enabled: shown
    opacity: progress
    clip: true

    Behavior on progress {
        NumberAnimation {
            id: reveal
            duration: root.shown ? Motion.enterDuration : Motion.exitDuration
            easing.type: Easing.OutCubic
        }
    }
    Connections {
        target: Motion
        function onEnterDurationChanged() { if (Motion.enterDuration === 0) reveal.complete(); }
        function onExitDurationChanged() { if (Motion.exitDuration === 0) reveal.complete(); }
    }
    ColumnLayout {
        id: content
        width: root.width
    }
}
