pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: root
    property int strength: 25
    property bool busy: false
    property int pendingStrength: -1
    function flush() {
        if (busy || pendingStrength < 0) return;
        const value = pendingStrength;
        pendingStrength = -1;
        request({op:"set_settings", settings:{stabilization:value}});
    }
    onBusyChanged: if (!busy) Qt.callLater(root.flush)
    signal request(var message)
    Layout.fillWidth: true
    spacing: 6
    Label { text: "Display"; font.bold: true }
    RowLayout {
        Label { text: "Stabilization"; Layout.fillWidth: true }
        Repeater {
            model: [{name:"Off", value:0}, {name:"Light", value:25}, {name:"Medium", value:60}, {name:"Strong", value:100}]
            delegate: Button {
                required property var modelData
                objectName: "stabilization_" + modelData.value
                text: modelData.name
                highlighted: root.strength === modelData.value
                enabled: !root.busy
                onClicked: root.request({op:"set_settings", settings:{stabilization:modelData.value}})
            }
        }
    }
    RowLayout {
        Slider {
            id: slider
            objectName: "stabilizationSlider"
            Layout.fillWidth: true
            from: 0; to: 100; stepSize: 1
            value: root.strength
            // Keep dragging while the previous value is acknowledged. Coalesce
            // pending changes so the final value is never dropped by Panel.busy.
            onMoved: { root.pendingStrength = Math.round(value); root.flush(); }
        }
        Label { text: Math.round(slider.value) + "%"; Layout.minimumWidth: 40 }
    }
    Label {
        text: "Steadies tiny head movements. Turns stay responsive. Changes apply immediately."
        opacity: 0.65; wrapMode: Text.WordWrap; Layout.fillWidth: true
    }
}
