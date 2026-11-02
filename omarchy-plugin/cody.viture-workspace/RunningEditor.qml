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
    readonly property var view: snapshot.view || ({})
    // The consented layout of this run (controller snapshot), never the editable startup profile.
    readonly property var desktops: snapshot.desktops || []
    signal request(var message)
    spacing:12
    RowLayout {
        Label { text:"Running · " + root.snapshot.model + " · " + root.snapshot.output; font.bold:true; Layout.fillWidth:true }
        Button { text:"Stop"; enabled:!root.busy; onClicked:root.request({op:"stop"}) }
    }
    Label { visible:root.snapshot.display_mode !== undefined || root.snapshot.uptime !== undefined; text:(root.snapshot.display_mode || "") + (root.snapshot.uptime !== undefined ? " · " + root.snapshot.uptime + " s" : "") }
    Rectangle {
        Layout.fillWidth:true; Layout.preferredHeight:135; color:Qt.darker(Commons.Color.background,1.2); radius:6
        Repeater {
            model:Model.previewRects(root.desktops,parent.width-24,96,10)
            delegate: Rectangle {
                id:tile
                required property var modelData
                required property int index
                x:modelData.x+12; y:modelData.y+18; width:modelData.width; height:modelData.height
                color:Qt.tint(Commons.Color.background,"#203c7050"); border.width:2; border.color:root.view.selected===index ? Commons.Color.accent : Commons.Color.muted
                Label { anchors.centerIn:parent; horizontalAlignment:Text.AlignHCenter; font.pixelSize:11; text:"XR-"+(tile.index+1)+"\n"+root.desktops[tile.index].width+"×"+root.desktops[tile.index].height+" · "+root.desktops[tile.index].scale*100+"%" }
                // Use only advertised relative operations; the panel waits for each reply.
                MouseArea { anchors.fill:parent; enabled:!root.busy && Model.selectOperations(root.view.selected,tile.index,root.snapshot.capabilities || [],root.desktops.length).length>0; onClicked:root.request(Model.selectOperations(root.view.selected,tile.index,root.snapshot.capabilities || [],root.desktops.length).map(op => ({op:"view_op",operation:op}))) }
            }
        }
    }
    Label { text:root.desktops.length + (root.desktops.length===1 ? " desktop" : " desktops") + " · count and geometry are fixed for this session; change them after Stop."; opacity:0.65; Layout.fillWidth:true; wrapMode:Text.WordWrap }
    Label { text:"View · " + (root.view.mode || "awaiting measured state") + (root.view.spacing !== undefined ? " · spacing " + root.view.spacing : ""); font.bold:true }
    RowLayout {
        visible:(root.snapshot.capabilities || []).indexOf("mode_next")>=0
        Repeater {
            model:["anchor","follow","locked"]
            delegate: Button {
                required property string modelData
                text:modelData; highlighted:root.view.mode===modelData; enabled:!root.busy
                onClicked:root.request({op:"set_settings",settings:{mode:modelData}})
            }
        }
    }
    Flow {
        Layout.fillWidth:true;  Layout.preferredHeight:childrenRect.height; spacing:8
        Repeater {
            model:root.snapshot.capabilities || []
            delegate: Button {
                required property string modelData
                text:({prev:"◀ Prev",next:"Next ▶",zoom_in:"Zoom in",zoom_out:"Overview / zoom out",recenter:"Recenter",mode_next:"Cycle Anchor / Follow / Locked",spacing_up:"Spacing +",spacing_down:"Spacing −",cursor_follow:"Cursor follow",prediction:"Pose prediction"})[modelData] || modelData
                highlighted:(modelData==="cursor_follow" && !!root.view.cursor_follow) || (modelData==="prediction" && !!root.view.prediction)
                enabled:!root.busy
                onClicked:root.request({op:"view_op",operation:modelData})
            }
        }
    }
    Label { text:"Measured, this session"; font.bold:true; visible:Object.keys(root.snapshot.status || {}).length>0 }
    Flow {
        Layout.fillWidth:true;  Layout.preferredHeight:childrenRect.height; spacing:12
        Repeater {
            model:Object.keys(root.snapshot.status || {})
            delegate: Label { required property string modelData; text:({presented_hz:"Presented (Hz)",missed:"Missed frames",capture_path:"Capture",pose_age_ms:"Pose age (ms)"}[modelData] || modelData) + ": " + root.snapshot.status[modelData] }
        }
    }
    Label { text:"Per-desktop position · size in space · curvature — coming"; enabled:false; opacity:0.45 }
}
