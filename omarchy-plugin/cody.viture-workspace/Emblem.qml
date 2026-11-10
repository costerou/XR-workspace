import QtQuick
import QtQuick.Shapes

// The one-colour mark (icon-symbolic.svg's arcs, dot and ring) drawn as shapes, so any colour tints it without a
// shader effect. Scaled from the SVG's 120×84 view.
Item {
    id: root
    property color color: "white"
    implicitWidth: 40; implicitHeight: 28
    Shape {
        width: 120; height: 84
        scale: Math.min(root.width / 120, root.height / 84)
        transformOrigin: Item.TopLeft
        x: (root.width - 120 * scale) / 2
        y: (root.height - 84 * scale) / 2
        preferredRendererType: Shape.CurveRenderer
        ShapePath {
            fillColor: root.color; strokeColor: "transparent"
            PathSvg { path: "M2.48,37.94 A64,64 0 0 1 33.97,7.53 L42.1,25.8 A44,44 0 0 0 20.45,46.71 Z M40.22,5.13 A64,64 0 0 1 79.78,5.13 L73.6,24.15 A44,44 0 0 0 46.4,24.15 Z M86.03,7.53 A64,64 0 0 1 117.52,37.94 L99.55,46.71 A44,44 0 0 0 77.9,25.8 Z M52,66 A8,8 0 1 0 68,66 A8,8 0 1 0 52,66 Z" }
        }
        ShapePath {
            fillColor: "transparent"; strokeColor: Qt.rgba(root.color.r, root.color.g, root.color.b, 0.6); strokeWidth: 5
            PathSvg { path: "M45.5,66 A14.5,14.5 0 1 0 74.5,66 A14.5,14.5 0 1 0 45.5,66 Z" }
        }
    }
}
