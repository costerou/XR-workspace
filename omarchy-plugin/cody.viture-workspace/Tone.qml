pragma Singleton
import QtQuick
import qs.Commons as Commons

// The panel's design tokens (docs/design-system/tokens.json): the Omarchy theme's colours, the plugin's warning and
// error (the shell theme has neither, only `urgent`), the foreground alphas, the "Panel" type styles, spacing and radius.
QtObject {
    readonly property color fg: Commons.Color.foreground
    readonly property color bg: Commons.Color.background
    readonly property color accent: Commons.Color.accent
    readonly property color muted: Commons.Color.muted
    readonly property color warn: "#e7c78a"
    readonly property color err: "#e06c75"

    // Foreground alphas, for Tone.a(): hairlines, control borders, card fills, secondary text, disabled content.
    readonly property real hairline: 0.12
    readonly property real border: 0.18
    readonly property real fill: 0.04
    readonly property real secondary: 0.65
    readonly property real disabled: 0.38

    // The ui- type styles by role, for PanelText's `role`: pixel size, weight and letter spacing.
    readonly property var type: ({
        cta: { size: 24, weight: Font.ExtraBold, letterSpacing: 1 },
        heading: { size: 22, weight: Font.Bold, letterSpacing: 0 },
        title: { size: 16, weight: Font.Bold, letterSpacing: 0 },
        strong: { size: 13, weight: Font.Bold, letterSpacing: 0 },
        body: { size: 12, weight: Font.Normal, letterSpacing: 0 },
        small: { size: 11, weight: Font.Normal, letterSpacing: 0 },
        caption: { size: 10, weight: Font.Bold, letterSpacing: 1 }
    })

    // space-1 to space-6.
    readonly property int space1: 4
    readonly property int space2: 8
    readonly property int space3: 12
    readonly property int space4: 16
    readonly property int space5: 24
    readonly property int space6: 32

    // The shell's corner radius (Hyprland's decoration:rounding), so the panel's corners match its windows'.
    readonly property int radius: Commons.Style.cornerRadius

    function a(alpha) { return Qt.rgba(fg.r, fg.g, fg.b, alpha) }
    function tint(color, alpha) { return Qt.rgba(color.r, color.g, color.b, alpha) }
    function of(tone) { return tone === "warn" ? warn : tone === "err" ? err : tone === "accent" ? accent : tone === "muted" ? muted : fg }
}
