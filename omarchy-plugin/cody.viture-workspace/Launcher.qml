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
    readonly property var limits: Model.desktopLimits(snapshot)
    signal request(var message)
    spacing: 12
    Label { text: "Not running · " + (root.snapshot.model || "VITURE") + " · " + (root.snapshot.output || "no presentation output"); font.bold:true }
    Button { text:"Start XR session"; highlighted:true; enabled:!root.busy; onClicked:root.request({op:"begin_start"}) }
    Label { text: (root.settings.desktops || []).length + " desktops · " + root.settings.mode + " · spacing " + root.settings.spacing + " · tracking " + (root.settings.tracking ? "on" : "off"); Layout.fillWidth:true; wrapMode:Text.WordWrap }
    RowLayout {
        visible: root.snapshot.fallback === "choose" || (root.snapshot.fallback_candidates || []).length > 1
        Label { text:"Return windows to" }
        ComboBox { model: root.snapshot.fallback_candidates || []; currentIndex: model.indexOf(root.settings.fallback || root.snapshot.fallback); onActivated:root.request({op:"set_settings", settings:{fallback:currentText}}) }
    }
    Label { visible:root.snapshot.fallback !== undefined; text:root.snapshot.fallback && root.snapshot.fallback !== "choose" ? "Fallback: " + root.snapshot.fallback : "Choose an available physical fallback before starting."; wrapMode:Text.WordWrap; Layout.fillWidth:true }
    Rectangle {
        Layout.fillWidth:true; Layout.preferredHeight:125; color:Qt.darker(Commons.Color.background,1.2); radius:6
        Repeater {
            model: Model.previewRects(root.settings.desktops || [], parent.width-24, 88, 10)
            delegate: Rectangle {
                id:tile
                required property var modelData
                required property int index
                x:modelData.x+12; y:modelData.y+18; width:modelData.width; height:modelData.height
                color:Qt.tint(Commons.Color.background,"#203c7050"); border.width:2; border.color:root.selected===index ? Commons.Color.accent : Commons.Color.muted
                Label { anchors.centerIn:parent; text:"XR-"+(tile.index+1)+"\n"+root.settings.desktops[tile.index].width+"×"+root.settings.desktops[tile.index].height+"\n"+root.settings.desktops[tile.index].scale*100+"%"; horizontalAlignment:Text.AlignHCenter; font.pixelSize:11 }
                MouseArea { anchors.fill:parent; onClicked:root.selected=tile.index }
            }
        }
    }
    RowLayout {
        Label { text:"Desktops" }
        SpinBox {
            objectName:"desktopCount"
            from:root.limits.min; to:root.limits.max
            value:(root.settings.desktops || []).length || root.limits.min
            enabled:!root.busy && !!root.settings.desktops
            onValueModified: {
                const count=value
                if (root.selected >= count) root.selected=-1
                root.request({op:"set_desktop_count",count:count})
                value=Qt.binding(() => (root.settings.desktops || []).length || root.limits.min)  // show what was saved
            }
        }
        Label { text:"Added desktops copy the last one. Applies to the next session."; opacity:0.65; Layout.fillWidth:true; wrapMode:Text.WordWrap }
    }
    Label { text:"Click a desktop to edit its startup geometry."; opacity:0.65 }
    Loader {
        Layout.fillWidth:true; active:root.selected>=0 && !!root.settings.desktops && root.selected<root.settings.desktops.length
        sourceComponent: DesktopEditor {
            desktop:root.settings.desktops[root.selected]; index:root.selected; busy:root.busy
            removable:Model.removeDesktop(root.settings.desktops,root.selected,root.limits)!==null
            onSave: function(desktop) { const ds=root.settings.desktops.slice(); ds[root.selected]=desktop; root.request({op:"set_settings",settings:{desktops:ds}}) }
            onRemove: { const ds=Model.removeDesktop(root.settings.desktops,root.selected,root.limits); if (ds) { root.selected=-1; root.request({op:"set_settings",settings:{desktops:ds}}) } }
        }
    }
    Label { visible:Model.largeLayoutWarning(root.settings.desktops || []); text:"Large layout: 120 Hz with this many pixels has not been measured."; color:"#e7c78a"; Layout.fillWidth:true; wrapMode:Text.WordWrap }
    Label { text:"Startup view"; font.bold:true }
    RowLayout {
        Repeater {
            model:["anchor","follow","locked"]
            delegate: Button { required property string modelData; text:modelData; highlighted:root.settings.mode===modelData; enabled:!root.busy; onClicked:root.request({op:"set_settings",settings:{mode:modelData}}) }
        }
        Label { text:"Spacing" }
        SpinBox { from:0; to:400; stepSize:20; value:root.settings.spacing || 0; enabled:!root.busy; onValueModified:root.request({op:"set_settings",settings:{spacing:value}}) }
    }
    Flow {
        Layout.fillWidth:true
        Layout.preferredHeight:childrenRect.height
        spacing:8
        CheckBox { text:"Cursor follow"; checked:!!root.settings.cursor_follow; enabled:!root.busy; onToggled:root.request({op:"set_settings",settings:{cursor_follow:checked}}) }
        CheckBox { text:"Pose prediction"; checked:!!root.settings.prediction; enabled:!root.busy; onToggled:root.request({op:"set_settings",settings:{prediction:checked}}) }
        CheckBox { text:"Head tracking"; checked:!!root.settings.tracking; enabled:!root.busy; onToggled:root.request({op:"set_settings",settings:{tracking:checked}}) }
        CheckBox { visible:root.snapshot.model==="luma-ultra"; text:"120 Hz mode switch"; checked:!!root.settings.mode_switch; enabled:!root.busy; onToggled:root.request({op:"set_settings",settings:{mode_switch:checked}}) }
    }
    Label { visible:root.snapshot.model==="beast"; text:"Beast: native 120 Hz mode, no switch. Presentation rate is measured during a run."; wrapMode:Text.WordWrap; Layout.fillWidth:true; opacity:0.65 }
}
