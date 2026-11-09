import QtQuick
import QtQuick.Controls

ComboBox {
    // Preserve the platform control's styling, focus, sizing and selection semantics.
    popup.opacity: 0
    popup.scale: 0.97
    popup.enter: MenuTransition {}
    popup.exit: MenuTransition { entering: false }
}
