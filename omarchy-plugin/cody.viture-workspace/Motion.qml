pragma Singleton
import QtQuick
import qs.Commons as Commons

QtObject {
    // Keep desktop popups, sections and page entrances on the same timing as XR menus.
    readonly property int enterDuration: Commons.Style.duration(180)
    readonly property int exitDuration: Commons.Style.duration(120)
}
