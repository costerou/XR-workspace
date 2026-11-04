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
    // The session's own desktops (a glass pill's Ultrawide / Add / Remove change them live) and their XR-n names.
    readonly property var desktops: snapshot.desktops || settings.desktops || []
    readonly property var names: snapshot.names || []
    // Live placements reported by the running session; the saved profile until the first status arrives.
    readonly property var placements: snapshot.placements || settings.placements || []
    readonly property var immersive: snapshot.immersive || ({})  // the run's 3D (Model.immersiveOn), {} before the first status
    readonly property var immersiveSetting: immersive.setting || settings.immersive || ({global:false,desktops:{}})
    property int placing: 0
    onDesktopsChanged: if (placing >= desktops.length) placing = 0  // a desktop removed in the glasses
    signal request(var message)
    spacing:12
    RowLayout {
        Label { text:"Running · " + root.snapshot.model + " · " + root.snapshot.output; font.bold:true; Layout.fillWidth:true }
        Button { text:"Stop"; enabled:!root.busy; onClicked:root.request({op:"stop"}) }
    }
    Label { visible:root.snapshot.display_mode !== undefined || root.snapshot.uptime !== undefined; text:(root.snapshot.display_mode || "") + (root.snapshot.uptime !== undefined ? " · " + root.snapshot.uptime + " s" : "") }
    Rectangle {
        Layout.fillWidth:true; Layout.preferredHeight:135; color:Qt.darker(Commons.Color.background,1.2); radius:6
        clip:true  // a desktop resized past the view (up to 10x) stays inside the preview
        // The tiles' negative z orders them among themselves; inside this plain Item they still draw above the
        // preview's fill (directly under the Rectangle a negative z put them beneath it, out of sight).
        Item {
            id:tiles
            anchors.fill:parent
            Repeater {
                model:Model.placedRects(Model.previewRects(root.desktops,tiles.width-24,96,10),root.placements)
                delegate: Rectangle {
                    id:tile
                    required property var modelData
                    required property int index
                    x:modelData.x+12; y:modelData.y+18; width:modelData.width; height:modelData.height
                    z:-modelData.width*modelData.height  // bigger behind: a huge desktop never hides a smaller one's click
                    color:Qt.tint(Commons.Color.background,"#203c7050"); border.width:2; border.color:root.view.selected===index ? Commons.Color.accent : Commons.Color.muted
                    Label { anchors.centerIn:parent; horizontalAlignment:Text.AlignHCenter; font.pixelSize:11; text:Model.desktopName(root.names,tile.index)+"\n"+root.desktops[tile.index].width+"×"+root.desktops[tile.index].height+" · "+root.desktops[tile.index].scale*100+"%" }
                    // Use only advertised relative operations; the panel waits for each reply.
                    MouseArea { anchors.fill:parent; enabled:!root.busy && Model.selectOperations(root.view.selected,tile.index,root.snapshot.capabilities || []).length>0; onClicked:root.request(Model.selectOperations(root.view.selected,tile.index,root.snapshot.capabilities || []).map(op => ({op:"view_op",operation:op}))) }
                }
            }
        }
    }
    Label { text:"Placement changes live. Ultrawide, Add new and Remove are on each desktop's glass pill in the glasses (Ctrl+Alt+L)."; opacity:0.65; Layout.fillWidth:true; wrapMode:Text.WordWrap }
    Label { text:"View · " + Model.viewLabel(root.view) + (root.view.spacing !== undefined ? " · spacing " + root.view.spacing : ""); font.bold:true }
    // The overview is always anchored; a maximized desktop follows you, or is locked to your view (maximize-follow).
    RowLayout {
        visible:(root.snapshot.capabilities || []).indexOf("mode_next")>=0
        Label { text:"Maximized:" }
        Repeater {
            model:["follow","locked"]
            delegate: Button {
                required property string modelData
                objectName:"maximized_" + modelData
                text:modelData; highlighted:!!root.view.zoomed && root.view.mode===modelData; enabled:!root.busy && !!root.view.zoomed
                onClicked:root.request({op:"set_settings",settings:{mode:modelData}})
            }
        }
        Label { visible:!root.view.zoomed; text:"(maximize a desktop first)"; opacity:0.65 }
    }
    Flow {
        Layout.fillWidth:true;  Layout.preferredHeight:childrenRect.height; spacing:8
        Repeater {
            model:root.snapshot.capabilities || []
            delegate: Button {
                required property string modelData
                text:Model.operationLabel(modelData)
                highlighted:modelData==="prediction" && !!root.view.prediction
                enabled:!root.busy
                onClicked:root.request({op:"view_op",operation:modelData})
            }
        }
    }
    RowLayout {
        objectName:"immersiveRow"
        Label { text:"3D"; font.bold:true }
        Button {
            objectName:"immersiveAll"
            text:"All desktops"; highlighted:(root.immersive.names || []).length===root.names.length && root.names.length>0; enabled:!root.busy
            onClicked:root.request({op:"set_settings",settings:{immersive:Model.immersiveAll(root.immersiveSetting,root.names)}})
        }
        Repeater {
            model:root.names
            delegate: Button {
                required property string modelData
                objectName:"immersive_" + modelData
                text:modelData; highlighted:(root.immersive.names || []).indexOf(modelData)>=0; enabled:!root.busy
                onClicked:root.request({op:"set_settings",settings:{immersive:Model.immersiveDesktop(root.immersiveSetting,modelData)}})
            }
        }
        Label { objectName:"immersiveState"; text:Model.immersiveLabel(root.snapshot.immersive); opacity:0.65; Layout.fillWidth:true; elide:Text.ElideRight }
    }
    Label { text:"Measured, this session"; font.bold:true; visible:Object.keys(root.snapshot.status || {}).length>0 }
    Flow {
        Layout.fillWidth:true;  Layout.preferredHeight:childrenRect.height; spacing:12
        Repeater {
            model:Object.keys(root.snapshot.status || {})
            delegate: Label { required property string modelData; text:({presented_hz:"Presented (Hz)",missed:"Missed frames",capture_path:"Capture",pose_age_ms:"Pose age (ms)",tracking:"Head tracking",capture_failure_run:"Capture failures in a row"}[modelData] || modelData) + ": " + root.snapshot.status[modelData] }
        }
    }
    RowLayout {
        Label { text:"Place" ; font.bold:true }
        Repeater {
            model:root.desktops.length
            delegate: Button {
                required property int index
                objectName:"place" + index
                text:Model.desktopName(root.names, index); highlighted:root.placing===index
                onClicked:root.placing=index
            }
        }
        Label { text:Model.placementSummary(root.placements[root.placing]); opacity:0.65; Layout.fillWidth:true; elide:Text.ElideRight }
    }
    PlacementEditor {
        objectName:"livePlacement"
        Layout.fillWidth:true
        live:true; busy:root.busy; index:root.placing; name:Model.desktopName(root.names, root.placing)
        placement:root.placements[root.placing] || Model.defaultPlacement()
        onSave: p => root.request({op:"set_settings",settings:{placements:Model.withPlacement(root.placements,root.placing,p,root.desktops.length)}})
    }
    VideoControls { Layout.fillWidth:true; snapshot:root.snapshot; busy:root.busy; onRequest:message => root.request(message) }
}
