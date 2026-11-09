import QtQuick

Transition {
    id: root
    property bool entering: true
    ParallelAnimation {
        NumberAnimation {
            property: "opacity"
            to: root.entering ? 1 : 0
            duration: root.entering ? Motion.enterDuration : Motion.exitDuration
            easing.type: Easing.OutCubic
        }
        NumberAnimation {
            property: "scale"
            to: root.entering ? 1 : 0.97
            duration: root.entering ? Motion.enterDuration : Motion.exitDuration
            easing.type: Easing.OutCubic
        }
    }
}
