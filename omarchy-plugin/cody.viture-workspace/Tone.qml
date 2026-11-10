pragma Singleton
import QtQuick
import qs.Commons as Commons

// The pre-start pages' colours: the Omarchy theme's, the plugin's warning and error, and the foreground at the alphas the
// mockups use (docs/mockups/plugin-prestart: hairlines 12%, borders 18%, fills 4%, secondary text 65%).
QtObject {
    readonly property color fg: Commons.Color.foreground
    readonly property color bg: Commons.Color.background
    readonly property color accent: Commons.Color.accent
    readonly property color muted: Commons.Color.muted
    readonly property color warn: "#e7c78a"
    readonly property color err: "#e06c75"
    function a(alpha) { return Qt.rgba(fg.r, fg.g, fg.b, alpha) }
    function tint(color, alpha) { return Qt.rgba(color.r, color.g, color.b, alpha) }
    function of(tone) { return tone === "warn" ? warn : tone === "err" ? err : tone === "accent" ? accent : tone === "muted" ? muted : fg }
}
