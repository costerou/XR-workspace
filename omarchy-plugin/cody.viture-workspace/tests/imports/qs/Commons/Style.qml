pragma Singleton
import QtQuick

// The offline runner has no shell/compositor. Keep the shell's duration contract.
QtObject {
    property bool reduceMotion: true
    function duration(ms) { return reduceMotion ? 0 : ms; }
}
