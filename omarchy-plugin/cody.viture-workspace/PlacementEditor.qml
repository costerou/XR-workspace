pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "Model.js" as Model

// Per-desktop placement in the glasses view: position round you, distance (size in space) and curvature. Every
// desktop always faces you, upright, so there is no facing setting.
// Idle: edits the saved startup placement. Live: each slider release applies it to the running session, which
// also saves it to this glasses model's profile. Presentation only; no permission scope changes.
ColumnLayout {
    id: root
    required property var placement
    property int index: 0
    property string name: ""  // the desktop's XR-n name while running (a removed desktop leaves a gap); else XR-(index+1)
    property bool busy: false
    property bool live: false
    property var draft: Model.placementEdit(placement) || Model.defaultPlacement()
    onPlacementChanged: draft = Model.placementEdit(placement) || Model.defaultPlacement()
    signal save(var placement)
    spacing: 6
    function setField(key, value) { const d = Object.assign({}, draft); d[key] = Math.round(value); draft = d }
    function apply() { const p = Model.placementEdit(draft); if (p && !busy) save(p) }
    Label { text: "Placement · " + (root.name || "XR-" + (root.index + 1)); font.bold: true }
    Repeater {
        objectName: "placementRows"
        model: [{key:"x"}, {key:"y"}, {key:"distance"}, {key:"curvature"}]
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
            Label { objectName: "placementValue_" + row.modelData.key; text: Model.placementValueText(row.modelData.key, root.draft[row.modelData.key]); Layout.preferredWidth: 68; horizontalAlignment: Text.AlignRight }
        }
    }
    Label {
        objectName: "placementHint"
        text: "Every desktop always faces you, upright, wherever it is, so there are no turning settings. Left / right and down / up move the desktop round you (180° is behind you, 90° up is overhead); dragging the glass pill under a desktop in the glasses' overview sets them, and in anchor mode a drag can take it anywhere round you without overlapping another desktop. Distance 200% is twice as far (half the apparent size); dragging a desktop's corner in the glasses' overview sets it too. Curvature bends the desktop into a horizontal arc without stretching it. Zooming into a desktop brings it flat in front of you; curvature stays."
        Layout.fillWidth: true; wrapMode: Text.WordWrap; opacity: 0.65
    }
    RowLayout {
        Button { objectName: "placementSave"; text: root.live ? "Apply placement" : "Save placement"; enabled: !root.busy && Model.placementEdit(root.draft) !== null; onClicked: root.apply() }
        Button { objectName: "placementReset"; text: "Flat"; enabled: !root.busy && !Model.isFlat(root.draft); onClicked: { root.draft = Model.defaultPlacement(); if (root.live) root.apply() } }
    }
}
