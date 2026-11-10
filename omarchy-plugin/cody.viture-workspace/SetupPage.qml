pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import qs.Commons
import qs.Ui as Ui
import "Model.js" as Model

// Everything a session starts with (docs/mockups/plugin-prestart 03): the desktops, what the session does, and comfort.
// Each control saves through the controller at once (op set_settings, or set_pro2_projection); the confirmation can only
// reduce these for one run. Rows that do not apply to the connected glasses are left out.
PageFrame {
    bodySpacing: 11
    id: root
    required property var snapshot
    required property var settings
    property bool busy: false
    property int selected: -1
    signal request(var message)
    signal select(int index)

    readonly property string model: snapshot.model || ""
    readonly property var desktops: settings.desktops || []
    readonly property var inUse: snapshot.tracking_in_use || {}
    readonly property var candidates: snapshot.fallback_candidates || []
    readonly property var pro2: snapshot.pro2 || null

    component SettingRow: RowLayout {
        id: row
        property string title: ""
        property string hint: ""
        property bool hintWarn: false
        default property alias control: slot.data
        Layout.fillWidth: true
        spacing: 12
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 2
            PanelText { text: row.title; font.pixelSize: 13 }
            PanelText { visible: text !== ""; text: row.hint; font.pixelSize: 11; alpha: 0.65; tone: row.hintWarn ? "warn" : "fg" }
        }
        RowLayout { id: slot; spacing: 8 }
    }
    component Hairline: Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: Tone.a(0.12) }

    SectionLabel { text: "Desktops at start" }
    Rectangle {
        objectName: "desktopPreview"
        Layout.fillWidth: true
        Layout.preferredHeight: 70
        color: Tone.a(0.04)
        border.width: 1; border.color: Tone.a(0.08)
        Row {
            anchors.centerIn: parent
            spacing: 10
            Repeater {
                objectName: "desktopTiles"
                model: root.desktops
                delegate: Rectangle {
                    id: tile
                    required property var modelData
                    required property int index
                    readonly property bool chosen: root.selected === index
                    objectName: "tile" + index
                    height: 52
                    width: Math.max(96, Math.min(150, Math.round(height * modelData.width / modelData.height * 0.95)))
                    color: chosen ? Tone.a(0.08) : "transparent"
                    border.width: chosen ? 2 : 1
                    border.color: chosen ? Tone.fg : Tone.a(0.25)
                    Column {
                        anchors.centerIn: parent
                        spacing: 1
                        PanelText { anchors.horizontalCenter: parent.horizontalCenter; text: "XR-" + (tile.index + 1); font.pixelSize: 11; font.bold: true }
                        PanelText { anchors.horizontalCenter: parent.horizontalCenter; text: tile.modelData.width + "×" + tile.modelData.height; font.pixelSize: 10; alpha: tile.chosen ? 1 : 0.65 }
                        PanelText { anchors.horizontalCenter: parent.horizontalCenter; text: Math.round(tile.modelData.scale * 100) + "%"; font.pixelSize: 10; alpha: tile.chosen ? 1 : 0.65 }
                    }
                    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: root.select(tile.index) }
                }
            }
        }
    }
    Loader {
        Layout.fillWidth: true
        active: root.selected >= 0 && root.selected < root.desktops.length
        visible: active
        sourceComponent: DesktopEditor {
            objectName: "desktopEditor"
            desktop: root.desktops[root.selected]; index: root.selected; busy: root.busy
            onSave: function(desktop) { const ds = root.desktops.slice(); ds[root.selected] = desktop; root.request({op: "set_settings", settings: {desktops: ds}}) }
        }
    }
    Rectangle {
        objectName: "editorHint"
        visible: !(root.selected >= 0 && root.selected < root.desktops.length)
        Layout.fillWidth: true
        Layout.preferredHeight: 92
        color: "transparent"
        border.width: 1; border.color: Tone.a(0.18)
        PanelText { anchors.centerIn: parent; width: parent.width - 40; horizontalAlignment: Text.AlignHCenter
                    text: "Click a desktop above to change its resolution and scale."; alpha: 0.55; font.pixelSize: 11 }
    }
    Note { objectName: "largeLayout"; visible: Model.largeLayoutWarning(root.desktops); tone: "warn"; text: "Large desktops: 120 Hz with this layout has not been measured." }
    PanelText {
        objectName: "pro2Note"
        visible: !!root.pro2
        text: Model.pro2SettingsLine(root.pro2) + " Change with pro2-settings."
        alpha: 0.55; font.pixelSize: 11
    }

    Hairline {}
    SectionLabel { text: "Session" }
    SettingRow {
        objectName: "trackingRow"
        title: "Head tracking"
        hint: root.model === "pro2" ? "Always on with the Pro 2 · " + (root.inUse.label || "Pro 2 SDK bridge")
            : root.model === "beast" ? (root.inUse.label || "XRLinuxDriver") + " · automatic for these glasses"
            : Model.driverMissing(root.snapshot, root.settings) ? (root.inUse.label || "XR Luma driver") + " · not installed"
            : root.inUse.label || ""
        hintWarn: Model.driverMissing(root.snapshot, root.settings)
        PanelText { visible: root.model === "pro2"; text: "On"; alpha: 0.65; Layout.fillWidth: false }
        Ui.ToggleSwitch {
            objectName: "trackingOption"
            visible: root.model !== "pro2"
            checked: !!root.settings.tracking
            interactive: !root.busy
            onToggled: root.request({op: "set_settings", settings: {tracking: !root.settings.tracking}})
        }
    }
    SettingRow {
        objectName: "modeSwitchRow"
        visible: root.model === "luma-ultra"
        title: "120 Hz"
        hint: "Switches " + (root.snapshot.output || "the glasses") + " to 1920×1200@120 for the session only"
        Ui.ToggleSwitch {
            objectName: "modeSwitchOption"
            checked: !!root.settings.mode_switch
            interactive: !root.busy
            onToggled: root.request({op: "set_settings", settings: {mode_switch: !root.settings.mode_switch}})
        }
    }
    SettingRow {
        objectName: "beastRate"
        visible: root.model === "beast"
        title: "Refresh rate"
        hint: "Native 120 Hz, no switch needed"
        PanelText { text: "120 Hz"; alpha: 0.65; Layout.fillWidth: false }
    }
    SettingRow {
        objectName: "pro2ProjectionOption"
        visible: !!root.pro2
        title: "Projection"
        hint: "Approximate, not calibrated"
        Ui.Dropdown {
            objectName: "pro2ProjectionCombo"
            showLabel: false
            implicitWidth: 110
            value: root.pro2 ? String(root.pro2.projection) : ""
            options: ((root.pro2 || {}).choices || []).map(p => ({value: String(p), label: p + "°"}))
            enabled: !root.busy
            onChanged: value => root.request({op: "set_pro2_projection", projection: Number(value)})
        }
    }
    SettingRow {
        objectName: "fallbackRow"
        title: "Return windows to"
        hint: "When XR stops or fails"
        Ui.Dropdown {
            objectName: "fallbackCombo"
            visible: root.candidates.length > 1
            showLabel: false
            implicitWidth: 150
            value: root.snapshot.fallback === "choose" ? "Choose monitor" : String(root.settings.fallback || root.snapshot.fallback || "")
            options: root.candidates
            enabled: !root.busy
            onChanged: value => root.request({op: "set_settings", settings: {fallback: value}})
        }
        PanelText {
            visible: root.candidates.length <= 1
            text: root.snapshot.fallback === null ? "No monitor" : root.snapshot.fallback || root.candidates[0] || "—"
            tone: root.snapshot.fallback === null ? "warn" : "fg"
            alpha: 0.85; Layout.fillWidth: false
        }
    }

    Hairline {}
    SectionLabel { text: "Comfort" }
    StabilizationSlider {
        strength: Model.stabilizationOf(root.settings)
        busy: root.busy
        onRequest: message => root.request(message)
    }
    Item { Layout.fillHeight: true }

    footer: PanelText { text: "Saved as you change them. Used by the next start."; alpha: 0.55; font.pixelSize: 11 }
}
