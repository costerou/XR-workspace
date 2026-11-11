import QtQuick
import QtQuick.Layouts
import qs.Commons

// Plain text in the shell's font, wrapping by default. `role` is a Tone.type style (body by default) and sets the size,
// weight and letter spacing; `tone` is a Tone.of name; `alpha` dims the foreground.
Text {
    property string role: "body"
    property string tone: "fg"
    property real alpha: 1
    readonly property var style: Tone.type[role] || Tone.type.body
    color: tone === "fg" ? Tone.a(alpha) : Tone.of(tone)
    font.family: Style.font.family
    font.pixelSize: style.size
    font.weight: style.weight
    font.letterSpacing: style.letterSpacing
    textFormat: Text.PlainText
    wrapMode: Text.Wrap
    Layout.fillWidth: true
}
