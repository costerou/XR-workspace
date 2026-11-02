pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "Model.js" as Model

// Per-desktop placement in the glasses view: position, distance (size in space), yaw/pitch/roll and curvature.
// Idle: edits the saved startup placement. Live: each slider release applies it to the running session, which
// also saves it to this glasses model's profile. Presentation only; no permission scope changes.
ColumnLayout {
    id: root
    required property var placement
    property int index: 0
    property bool busy: false
    property bool live: false
    property var draft: Model.placementEdit(placement) || Model.defaultPlacement()
    onPlacementChanged: draft = Model.placementEdit(placement) || Model.defaultPlacement()
    signal save(var placement)
    spacing: 6
    function setField(key, value) { const d = Object.assign({}, draft); d[key] = Math.round(value); draft = d }
    function apply() { const p = Model.placementEdit(draft); if (p && !busy) save(p) }
    Label { text: "Placement · XR-" + (root.index + 1); font.bold: true }
    Repeater {
        model: [
            {key:"x", unit:"%"},
            {key:"y", unit:"%"},
            {key:"distance", unit:"%"},
            {key:"yaw", unit:"°"},
            {key:"pitch", unit:"°"},
            {key:"roll", unit:"°"},
            {key:"curvature", unit:"°"}
        ]
        delegate: RowLayout {
            id: row
            required property var modelData
            Layout.fillWidth: true
            Label { objectName: "placementLabel_" + row.modelData.key; text: Model.placementLabel(row.modelData.key); Layout.preferredWidth: 150 }
            Slider {
                objectName: "placement_" + row.modelData.key
                Layout.fillWidth: true
                from: Model.placementBounds(row.modelData.key)[0]
                to: Model.placementBounds(row.modelData.key)[1]
                stepSize: 1
                snapMode: Slider.SnapAlways
                value: root.draft[row.modelData.key]
                enabled: !root.busy
                onMoved: root.setField(row.modelData.key, value)
                onPressedChanged: if (!pressed && root.live) root.apply()
            }
            Label { text: root.draft[row.modelData.key] + row.modelData.unit; Layout.preferredWidth: 52; horizontalAlignment: Text.AlignRight }
        }
    }
    Label {
        text: "Offsets are a share of the desktop's own size. Distance 200% is twice as far (half the apparent size). Positive yaw faces right, positive pitch tilts the top toward you. Curvature bends the desktop into a horizontal arc without stretching it. Zooming into a desktop brings it flat in front of you; curvature stays."
        Layout.fillWidth: true; wrapMode: Text.WordWrap; opacity: 0.65
    }
    RowLayout {
        Button { objectName: "placementSave"; text: root.live ? "Apply placement" : "Save placement"; enabled: !root.busy && Model.placementEdit(root.draft) !== null; onClicked: root.apply() }
        Button { objectName: "placementReset"; text: "Flat"; enabled: !root.busy && !Model.isFlat(root.draft); onClicked: { root.draft = Model.defaultPlacement(); if (root.live) root.apply() } }
    }
}
