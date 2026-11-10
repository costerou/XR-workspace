pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import qs.Commons
import qs.Ui as Ui
import "Model.js" as Model

// Setup's stabilization: one slider with the four named stops under it (a stop's name sets its strength). A drag while a
// save is in flight is kept and sent once the panel is free, so the last value is never dropped.
RowLayout {
    id: root
    property int strength: 25
    property bool busy: false
    property int pendingStrength: -1
    signal request(var message)
    function send(value) { pendingStrength = Math.round(value); flush() }
    function flush() {
        if (busy || pendingStrength < 0) return
        const value = pendingStrength
        pendingStrength = -1
        request({op: "set_settings", settings: {stabilization: value}})
    }
    onBusyChanged: if (!busy) Qt.callLater(root.flush)
    Layout.fillWidth: true
    spacing: 14
    ColumnLayout {
        Layout.fillWidth: true
        spacing: 2
        PanelText { text: "Stabilization"; font.pixelSize: 13 }
        PanelText { text: "Steadies small head movements."; font.pixelSize: 11; alpha: 0.65 }
    }
    Item {
        Layout.preferredWidth: 200
        Layout.preferredHeight: 40
        Ui.PanelSlider {
            id: slider
            objectName: "stabilizationSlider"
            width: parent.width
            y: 0
            minimum: 0; maximum: 100; step: 1; integer: true
            value: root.strength
            trackColor: Tone.a(0.18)
            onMoved: value => root.send(value)
            onReleased: value => root.send(value)
        }
        Repeater {
            model: Model.STABILIZATION_STOPS
            delegate: Text {
                id: stop
                required property var modelData
                readonly property bool current: Model.stabilizationName(Math.round(slider.liveValue)) === modelData.name
                objectName: "stabilization_" + modelData.value
                x: Math.max(0, Math.min(parent.width - width, slider.knobSize / 2 + (parent.width - slider.knobSize) * modelData.value / 100 - width / 2))
                y: 24
                text: modelData.name
                color: current ? Tone.fg : Tone.a(0.55)
                font.family: Style.font.family
                font.pixelSize: 10
                font.bold: current
                MouseArea { anchors.fill: parent; anchors.margins: -4; cursorShape: Qt.PointingHandCursor; onClicked: root.send(stop.modelData.value) }
            }
        }
    }
    PanelText { objectName: "stabilizationValue"; text: Math.round(slider.liveValue) + "%"; Layout.fillWidth: false; Layout.preferredWidth: 36
                horizontalAlignment: Text.AlignRight; Layout.alignment: Qt.AlignTop }
}
