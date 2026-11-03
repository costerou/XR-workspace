pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import qs.Commons as Commons
import "Model.js" as Model

ColumnLayout {
    id: root
    required property var snapshot
    required property var settings
    property bool busy: false
    property int selected: -1
    signal request(var message)
    spacing: 12
    Label { text: "Not running · " + (root.snapshot.model || "VITURE") + " · " + (root.snapshot.output || "no presentation output"); font.bold:true }
    Button { text:"Start XR session"; highlighted:true; enabled:!root.busy; onClicked:root.request({op:"begin_start"}) }
    Label { text: (root.settings.desktops || []).length + " desktops · head tracking " + (root.settings.tracking ? "on" : "off"); Layout.fillWidth:true; wrapMode:Text.WordWrap }
    RowLayout {
        visible: root.snapshot.fallback === "choose" || (root.snapshot.fallback_candidates || []).length > 1
        Label { text:"Return windows to" }
        ComboBox { model: root.snapshot.fallback_candidates || []; currentIndex: model.indexOf(root.settings.fallback || root.snapshot.fallback); onActivated:root.request({op:"set_settings", settings:{fallback:currentText}}) }
    }
    Label { visible:root.snapshot.fallback !== undefined; text:root.snapshot.fallback && root.snapshot.fallback !== "choose" ? "Fallback: " + root.snapshot.fallback : "Choose an available physical fallback before starting."; wrapMode:Text.WordWrap; Layout.fillWidth:true }
    Rectangle {
        Layout.fillWidth:true; Layout.preferredHeight:125; color:Qt.darker(Commons.Color.background,1.2); radius:6
        clip:true  // a desktop resized past the view (up to 10x) stays inside the preview
        Repeater {
            model: Model.placedRects(Model.previewRects(root.settings.desktops || [], parent.width-24, 88, 10), root.settings.placements)
            delegate: Rectangle {
                id:tile
                required property var modelData
                required property int index
                x:modelData.x+12; y:modelData.y+18; width:modelData.width; height:modelData.height
                z:-modelData.width*modelData.height  // bigger behind: a huge desktop never hides a smaller one's click
                color:Qt.tint(Commons.Color.background,"#203c7050"); border.width:2; border.color:root.selected===index ? Commons.Color.accent : Commons.Color.muted
                Label { anchors.centerIn:parent; text:"XR-"+(tile.index+1)+"\n"+root.settings.desktops[tile.index].width+"×"+root.settings.desktops[tile.index].height+"\n"+root.settings.desktops[tile.index].scale*100+"%"; horizontalAlignment:Text.AlignHCenter; font.pixelSize:11 }
                MouseArea { anchors.fill:parent; onClicked:root.selected=tile.index }
            }
        }
    }
    Label { text:"Click a desktop to edit its startup resolution and scale."; opacity:0.65 }
    // Everything else about the desktops is done in the glasses (README "Placing desktops"): open a desktop's glass pill
    // (Ctrl+Alt+L) for its placement, Ultrawide, Add new and Remove; Ctrl+Alt+V, [ / ], F and P set the view.
    Label { text:"Placement, ultrawide, adding or removing desktops, view mode, spacing, cursor follow and pose prediction are set in the glasses: open a desktop's glass pill (Ctrl+Alt+L), or press Ctrl+Alt+H for the keys."; opacity:0.65; Layout.fillWidth:true; wrapMode:Text.WordWrap }
    Loader {
        Layout.fillWidth:true; active:root.selected>=0 && !!root.settings.desktops
        sourceComponent: DesktopEditor {
            desktop:root.settings.desktops[root.selected]; index:root.selected; busy:root.busy
            onSave: function(desktop) { const ds=root.settings.desktops.slice(); ds[root.selected]=desktop; root.request({op:"set_settings",settings:{desktops:ds}}) }
        }
    }
    Label { visible:Model.largeLayoutWarning(root.settings.desktops || []); text:"Large desktops: 120 Hz with this layout has not been measured."; color:"#e7c78a"; Layout.fillWidth:true; wrapMode:Text.WordWrap }
    Label { text:"Before starting"; font.bold:true }
    Flow {
        Layout.fillWidth:true
        Layout.preferredHeight:childrenRect.height
        spacing:8
        CheckBox { text:"Head tracking"; checked:!!root.settings.tracking; enabled:!root.busy; onToggled:root.request({op:"set_settings",settings:{tracking:checked}}) }
        CheckBox { visible:root.snapshot.model==="luma-ultra"; text:"120 Hz mode switch"; checked:!!root.settings.mode_switch; enabled:!root.busy; onToggled:root.request({op:"set_settings",settings:{mode_switch:checked}}) }
    }
    Label { visible:root.snapshot.model==="beast"; text:"Beast: native 120 Hz mode, no switch. Presentation rate is measured during a run."; wrapMode:Text.WordWrap; Layout.fillWidth:true; opacity:0.65 }
}
