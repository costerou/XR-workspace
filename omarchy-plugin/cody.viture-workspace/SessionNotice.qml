pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: root
    required property var snapshot
    property bool busy: false
    signal request(var message)
    Label { text:({"no-glasses":"No VITURE glasses detected. Plug in a Beast or Luma Ultra.","recovery-needed":"A previous session needs recovery.","cli-recovery":"Needs CLI recovery. Use the existing CLI recovery procedure.",starting:"Starting session…",stopping:"Stopping session…",failed:"Session failed"})[root.snapshot.state] || "Waiting for controller state…"; Layout.fillWidth:true; wrapMode:Text.WordWrap }
    Label { visible:root.snapshot.state==="cli-recovery"; text:root.snapshot.journal || ""; Layout.fillWidth:true; wrapMode:Text.WrapAnywhere }
    Button { visible:root.snapshot.state==="starting" || (root.snapshot.state==="failed" && !!root.snapshot.run_id); text:"Cancel / stop session"; enabled:!root.busy; onClicked:root.request({op:"stop"}) }
    Button { objectName:"recoverButton"; visible:root.snapshot.state==="recovery-needed"; text:"Recover"; enabled:!root.busy; onClicked:root.request({op:"begin_recover"}) }
}
