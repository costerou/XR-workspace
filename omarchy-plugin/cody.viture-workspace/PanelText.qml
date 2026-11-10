import QtQuick
import QtQuick.Layouts
import qs.Commons

// Plain text in the shell's font, wrapping by default. `tone` is a Tone.of name; `alpha` dims the foreground.
Text {
    property string tone: "fg"
    property real alpha: 1
    color: tone === "fg" ? Tone.a(alpha) : Tone.of(tone)
    font.family: Style.font.family
    font.pixelSize: 12
    textFormat: Text.PlainText
    wrapMode: Text.Wrap
    Layout.fillWidth: true
}
