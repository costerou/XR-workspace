pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import qs.Commons
import qs.Ui as Ui
import "Model.js" as Model

// One desktop's startup size and scale, always three rows so the page does not jump: shape, then its resolution (or the
// custom pixels), then the scale. A preset or a scale pick saves at once; custom pixels wait for Apply. Scales that do
// not give whole logical pixels are struck through.
ColumnLayout {
    id: root
    required property var desktop
    property int index: 0
    property bool busy: false
    property string shape: Model.shapeOf(desktop.width, desktop.height)
    property int draftWidth: desktop.width
    property int draftHeight: desktop.height
    property real draftScale: desktop.scale
    onDesktopChanged: { draftWidth=desktop.width; draftHeight=desktop.height; draftScale=desktop.scale; shape=Model.shapeOf(desktop.width,desktop.height) }
    signal save(var desktop)
    readonly property var valid: Model.desktopEdit(draftWidth, draftHeight, draftScale)
    readonly property bool sizeValid: Model.desktopEdit(draftWidth, draftHeight, 1) !== null
    readonly property var scales: Model.validScales(draftWidth, draftHeight)
    spacing: 8

    function commit(width, height, scale) {
        draftWidth = width; draftHeight = height; draftScale = scale
        const edit = Model.desktopEdit(width, height, scale)
        if (edit && !busy) save(edit)
    }

    component RowLabel: ColumnLayout {
        property alias text: main.text
        property alias hint: sub.text
        property bool hintError: false
        spacing: 2
        Layout.fillWidth: true
        PanelText { id: main; font.pixelSize: 13; wrapMode: Text.NoWrap }
        PanelText { id: sub; visible: text !== ""; font.pixelSize: 11; tone: parent.hintError ? "err" : "fg"; alpha: 0.65; wrapMode: Text.NoWrap }
    }

    RowLayout {
        Layout.preferredHeight: 32
        RowLabel { text: "XR-" + (root.index + 1) + " shape" }
        Segmented {
            objectName: "shapeChoice"
            enabled: !root.busy  // commit() shows a pick and saves it only when idle
            value: root.shape
            options: [{value: "16:9", label: "16:9"}, {value: "21:9", label: "21:9"}, {value: "32:9", label: "32:9"}, {value: "custom", label: "Custom"}]
            onPicked: value => {
                root.shape = value
                const p = Model.presetsFor(value)
                if (p.length) root.commit(p[0].width, p[0].height, Model.validScales(p[0].width, p[0].height).indexOf(root.draftScale) >= 0 ? root.draftScale : 1)
            }
        }
    }
    RowLayout {
        Layout.preferredHeight: 32
        visible: root.shape !== "custom"
        RowLabel { text: "Resolution" }
        Ui.Dropdown {
            objectName: "resolutionChoice"
            showLabel: false
            implicitWidth: 170
            value: root.draftWidth + "x" + root.draftHeight
            options: Model.presetsFor(root.shape).map(p => ({value: p.width + "x" + p.height, label: p.width + " × " + p.height}))
            enabled: !root.busy
            onChanged: value => {
                const wh = value.split("x").map(Number)
                root.commit(wh[0], wh[1], Model.validScales(wh[0], wh[1]).indexOf(root.draftScale) >= 0 ? root.draftScale : 1)
            }
        }
    }
    RowLayout {
        Layout.preferredHeight: 32
        visible: root.shape === "custom"
        spacing: 8
        RowLabel { text: "Pixels"; hint: "Even numbers, 640–7680 × 480–4320"; hintError: !root.sizeValid }
        Ui.TextField {
            objectName: "customWidth"
            Layout.preferredWidth: 84
            text: String(root.draftWidth)
            inputMethodHints: Qt.ImhDigitsOnly
            foreground: Number(text) >= 640 && Number(text) <= 7680 && Number(text) % 2 === 0 ? Tone.fg : Tone.err
            onTextEdited: root.draftWidth = Number(text) || 0
        }
        PanelText { text: "×"; Layout.fillWidth: false; alpha: 0.65 }
        Ui.TextField {
            objectName: "customHeight"
            Layout.preferredWidth: 84
            text: String(root.draftHeight)
            inputMethodHints: Qt.ImhDigitsOnly
            foreground: Number(text) >= 480 && Number(text) <= 4320 && Number(text) % 2 === 0 ? Tone.fg : Tone.err
            onTextEdited: root.draftHeight = Number(text) || 0
        }
        Ui.Button {
            objectName: "applyCustom"
            text: "Apply"
            bordered: true
            enabled: !root.busy && root.valid !== null
            opacity: enabled ? 1 : 0.4
            onClicked: root.save(root.valid)
        }
    }
    RowLayout {
        Layout.preferredHeight: 32
        RowLabel { text: "Scale" }
        Segmented {
            objectName: "scaleChoice"
            enabled: !root.busy
            value: root.draftScale
            options: Model.SCALES.map(s => ({value: s, label: String(Math.round(s * 100)), disabled: root.scales.indexOf(s) < 0}))
            onPicked: value => { if (root.shape === "custom") root.draftScale = value; else root.commit(root.draftWidth, root.draftHeight, value) }
        }
    }
    PanelText {
        objectName: "scaleHint"
        visible: root.scales.length < Model.SCALES.length
        text: "Struck-through scales do not give whole pixels at this size."
        alpha: 0.55; font.pixelSize: 11
    }
}
