pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "Model.js" as Model

// The panel before a session: one big Start. Desktops, placement, view, video and the rest are set in the glasses
// during the session (README "Everyday use" and "Placing desktops"). The few things only the panel can set before a
// start (each desktop's startup resolution and scale, head tracking and its driver, the 120 Hz switch, which monitor windows return
// to) wait under "Startup options", closed each time the panel shows this page. Start only asks the controller to
// begin (op begin_start); the one-run consent box (ConfirmDialog) follows as before.
// Colours come from the palette Panel.qml sets from the Omarchy theme, so this page needs no shell-only imports.
ColumnLayout {
    id: root
    required property var snapshot
    required property var settings
    property bool busy: false
    property int selected: -1
    // A desktop asked for when the panel opened (Panel.open({desktop:n})) has its editor here, so open with it.
    property bool showOptions: selected >= 0
    readonly property var candidates: snapshot.fallback_candidates || []
    // begin_start refuses both (tools/xr_controller/state.py): "choose" needs a pick first, and null leaves no physical
    // monitor for windows to return to. A snapshot without the field (an older controller) is left to the controller.
    readonly property bool fallbackRequired: snapshot.fallback === "choose"
    readonly property bool noFallback: snapshot.fallback === null
    signal request(var message)
    spacing: 14

    component FallbackRow: RowLayout {
        spacing: 10
        Label { text: "Return windows to" }
        ComboBox {
            objectName: "fallbackCombo"
            model: root.candidates
            currentIndex: model.indexOf(root.settings.fallback || root.snapshot.fallback)
            enabled: !root.busy
            onActivated: root.request({op:"set_settings", settings:{fallback:currentText}})
        }
    }

    // The project logo, large: this page's header shows only Close (Panel.qml).
    Logo {
        objectName: "heroLogo"
        size: 96
        Layout.alignment: Qt.AlignHCenter
        Layout.topMargin: 8
    }
    Button {
        id: start
        objectName: "startButton"
        text: "Start XR"
        Layout.fillWidth: true
        Layout.maximumWidth: 460
        Layout.preferredHeight: 76
        Layout.topMargin: 20
        Layout.alignment: Qt.AlignHCenter
        font.pixelSize: 24
        font.bold: true
        enabled: !root.busy && !root.fallbackRequired && !root.noFallback
        onClicked: root.request({op:"begin_start"})
        background: Rectangle {
            radius: 14
            color: !start.enabled ? start.palette.button
                 : start.down ? Qt.darker(start.palette.highlight, 1.2)
                 : start.hovered ? Qt.lighter(start.palette.highlight, 1.1)
                 : start.palette.highlight
            border.width: start.visualFocus ? 2 : 0
            border.color: start.palette.windowText
        }
        contentItem: Label {
            text: start.text
            font: start.font
            color: start.enabled ? start.palette.highlightedText : start.palette.disabled.buttonText
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
    }
    Label {
        objectName: "startStatus"
        text: Model.startStatus(root.snapshot)
        Layout.alignment: Qt.AlignHCenter
        opacity: 0.65
    }
    FallbackRow {
        objectName: "fallbackRequired"
        visible: root.fallbackRequired
        Layout.alignment: Qt.AlignHCenter
    }
    Label {
        objectName: "noFallback"
        visible: root.noFallback
        text: "Start needs a normal monitor besides the glasses: your windows go back to it when the session stops."
        color: "#e7c78a"
        horizontalAlignment: Text.AlignHCenter
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
    }
    Label {
        objectName: "inXrHint"
        text: "Everything else is set in the glasses during the session. Press Ctrl+Alt+H there for the keys."
        opacity: 0.65
        horizontalAlignment: Text.AlignHCenter
        Layout.fillWidth: true
        Layout.topMargin: 6
        wrapMode: Text.WordWrap
    }
    Button {
        objectName: "startupOptionsToggle"
        flat: true
        text: (root.showOptions ? "▾  " : "▸  ") + "Startup options"
        Layout.alignment: Qt.AlignHCenter
        opacity: 0.8
        onClicked: root.showOptions = !root.showOptions
    }

    // Only what cannot change once a session runs. Each control saves through the controller (op set_settings).
    ColumnLayout {
        objectName: "startupOptions"
        visible: root.showOptions
        Layout.fillWidth: true
        spacing: 12
        FallbackRow {
            objectName: "fallbackOption"
            visible: !root.fallbackRequired && root.candidates.length > 1
        }
        Rectangle {
            objectName: "desktopPreview"
            Layout.fillWidth: true; Layout.preferredHeight: 125; color: root.palette.base; radius: 6
            clip: true  // a desktop resized past the view (up to 10x) stays inside the preview
            // The tiles' negative z orders them among themselves; inside this plain Item they still draw above the
            // preview's fill (a negative z directly under the Rectangle would put them beneath it, out of sight).
            Item {
                id: tiles
                anchors.fill: parent
                Repeater {
                    objectName: "desktopTiles"
                    model: Model.placedRects(Model.previewRects(root.settings.desktops || [], tiles.width-24, 88, 10), root.settings.placements)
                    delegate: Rectangle {
                        id: tile
                        required property var modelData
                        required property int index
                        x: modelData.x+12; y: modelData.y+18; width: modelData.width; height: modelData.height
                        z: -modelData.width*modelData.height  // bigger behind: a huge desktop never hides a smaller one's click
                        color: Qt.tint(root.palette.window, "#203c7050"); border.width: 2
                        border.color: root.selected===index ? root.palette.highlight : root.palette.disabled.windowText
                        Label { anchors.centerIn: parent; text: "XR-"+(tile.index+1)+"\n"+root.settings.desktops[tile.index].width+"×"+root.settings.desktops[tile.index].height+"\n"+root.settings.desktops[tile.index].scale*100+"%"; horizontalAlignment: Text.AlignHCenter; font.pixelSize: 11 }
                        MouseArea { anchors.fill: parent; onClicked: root.selected=tile.index }
                    }
                }
            }
        }
        Label { text: "Click a desktop to set its resolution and scale for the next start."; opacity: 0.65; Layout.fillWidth: true; wrapMode: Text.WordWrap }
        Loader {
            Layout.fillWidth: true; active: root.selected>=0 && !!root.settings.desktops
            sourceComponent: DesktopEditor {
                objectName: "desktopEditor"
                desktop: root.settings.desktops[root.selected]; index: root.selected; busy: root.busy
                onSave: function(desktop) { const ds=root.settings.desktops.slice(); ds[root.selected]=desktop; root.request({op:"set_settings",settings:{desktops:ds}}) }
            }
        }
        Label { visible: Model.largeLayoutWarning(root.settings.desktops || []); text: "Large desktops: 120 Hz with this layout has not been measured."; color: "#e7c78a"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
        Flow {
            Layout.fillWidth: true
            Layout.preferredHeight: childrenRect.height
            spacing: 8
            CheckBox { objectName: "trackingOption"; text: "Head tracking"; checked: !!root.settings.tracking; enabled: !root.busy; onToggled: root.request({op:"set_settings",settings:{tracking:checked}}) }
            CheckBox { objectName: "modeSwitchOption"; visible: root.snapshot.model==="luma-ultra"; text: "120 Hz mode switch"; checked: !!root.settings.mode_switch; enabled: !root.busy; onToggled: root.request({op:"set_settings",settings:{mode_switch:checked}}) }
        }
        // Offered once a build with the XR Luma driver is installed (the controller's luma_available); XRLinuxDriver stays the default.
        RowLayout {
            objectName: "trackingDriverOption"
            visible: !!root.settings.tracking && (!!root.snapshot.luma_available || root.settings.tracking_driver === "luma")
            spacing: 10
            Label { text: "Tracking driver" }
            ComboBox {
                objectName: "trackingDriverCombo"
                textRole: "text"; valueRole: "value"
                model: [{text:"XRLinuxDriver", value:"xrdriver"}, {text:"XR Luma (test)", value:"luma"}]
                currentIndex: root.settings.tracking_driver === "luma" ? 1 : 0
                enabled: !root.busy
                onActivated: root.request({op:"set_settings", settings:{tracking_driver:currentValue}})
            }
        }
        Label { objectName: "lumaMissing"; visible: !!root.settings.tracking && root.settings.tracking_driver === "luma" && root.snapshot.luma_available === false; text: "The XR Luma driver is not installed: starts use XRLinuxDriver."; color: "#e7c78a"; Layout.fillWidth: true; wrapMode: Text.WordWrap }
        Label { objectName: "beastNote"; visible: root.snapshot.model==="beast"; text: "Beast: native 120 Hz mode, no switch. Presentation rate is measured during a run."; wrapMode: Text.WordWrap; Layout.fillWidth: true; opacity: 0.65 }
    }
}
