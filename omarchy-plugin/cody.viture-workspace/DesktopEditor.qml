pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "Model.js" as Model

ColumnLayout {
    id: root
    required property var desktop
    property var placement: Model.defaultPlacement()
    property int index: 0
    property bool busy: false
    property string shape: Model.shapeOf(desktop.width, desktop.height)
    property int draftWidth: desktop.width
    property int draftHeight: desktop.height
    property real draftScale: desktop.scale
    onDesktopChanged: { draftWidth=desktop.width; draftHeight=desktop.height; draftScale=desktop.scale; shape=Model.shapeOf(desktop.width,desktop.height) }
    signal save(var desktop)
    signal savePlacement(var placement)
    spacing: 8
    Label { text: "XR-" + (root.index+1); font.bold: true }
    RowLayout {
        Label { text: "Shape"; Layout.preferredWidth: 95 }
        ComboBox {
            model: ["16:9", "21:9", "32:9", "custom"]
            currentIndex: model.indexOf(root.shape)
            onActivated: { root.shape=currentText; const p = Model.presetsFor(currentText); if (p.length) { root.draftWidth=p[0].width; root.draftHeight=p[0].height; root.draftScale=1 } }
        }
        ComboBox {
            model: Model.presetsFor(root.shape).map(p => p.width + " × " + p.height)
            currentIndex: model.indexOf(root.draftWidth + " × " + root.draftHeight)
            onActivated: { const p=Model.presetsFor(root.shape)[currentIndex]; root.draftWidth=p.width; root.draftHeight=p.height; root.draftScale=1 }
        }
    }
    RowLayout {
        Label { text: "Pixels"; Layout.preferredWidth: 95 }
        TextField { Layout.preferredWidth: 110; text: String(root.draftWidth); validator: IntValidator { bottom:640; top:7680 } onTextEdited: root.draftWidth=Number(text) }
        Label { text: "×" }
        TextField { Layout.preferredWidth: 110; text: String(root.draftHeight); validator: IntValidator { bottom:480; top:4320 } onTextEdited: root.draftHeight=Number(text) }
    }
    RowLayout {
        Label { text: "Scale"; Layout.preferredWidth: 95 }
        Repeater {
            model: [1,1.25,1.5,1.75,2]
            delegate: Button {
                required property real modelData
                objectName:"scale" + modelData
                text: modelData*100 + "%"
                opacity:enabled ? 1 : 0.35
                highlighted: root.draftScale === modelData
                enabled: Model.validScales(root.draftWidth,root.draftHeight).indexOf(modelData)>=0
                onClicked: root.draftScale=modelData
            }
        }
    }
    Label { text: "Disabled scales do not produce whole logical pixels. Custom sizes must be even, 640–7680 × 480–4320."; Layout.fillWidth:true; wrapMode:Text.WordWrap; opacity:0.65 }
    Button { text:"Save desktop"; enabled: !root.busy && Model.desktopEdit(root.draftWidth,root.draftHeight,root.draftScale)!==null; onClicked:root.save(Model.desktopEdit(root.draftWidth,root.draftHeight,root.draftScale)) }
    PlacementEditor { Layout.fillWidth:true; Layout.topMargin:8; placement:root.placement; index:root.index; busy:root.busy; onSave: p => root.savePlacement(p) }
    Label { text:"Add / remove desktops — coming"; enabled:false; opacity:0.45 }
}
