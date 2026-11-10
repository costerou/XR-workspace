pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import Quickshell
import qs.Commons
import qs.Ui as Ui
import "Model.js" as Model

// An earlier session left something behind (docs/mockups/plugin-prestart 06): recover it here, from a terminal, or wait
// for the other session to end. Start stays away until it is dealt with.
PageFrame {
    id: root
    required property var snapshot
    property bool busy: false
    signal request(var message)
    readonly property string state: snapshot.state || ""
    readonly property string journal: snapshot.journal || ""
    readonly property string command: "python -m tools.native_spike recover --journal " + (journal || "JOURNAL") + " --instance INSTANCE --output OUTPUT --consent-file CONSENT"
    readonly property string unfinished: {
        const r = String(snapshot.reason || "")
        return r.indexOf("Recover could not finish") === 0 ? r.replace(/^Recover could not finish\.?\s*/, "") : ""
    }
    function copy(text) { Quickshell.execDetached(["wl-copy", text]) }

    DeviceCard { objectName: "deviceCard"; info: Model.deviceCard(root.snapshot, true) }
    PageHeading {
        title: root.state === "recovery-needed" ? "Finish the last session first"
             : root.state === "cli-recovery" ? "Recover from a terminal" : "The glasses are in use"
        text: root.state === "recovery-needed" ? "A session ended without cleaning up. Recover puts your windows back and removes its XR desktops."
            : root.state === "cli-recovery" ? "The panel cannot recover this one itself: more than one unfinished session, or a journal it cannot read. Run this once per journal:"
            : "A session started outside XR Workspace is using them. Stop it where it was started."
    }

    Note {
        objectName: "unfinishedNote"
        visible: root.state === "recovery-needed" && root.unfinished !== ""
        tone: "warn"
        title: "Last attempt could not finish"
        text: root.unfinished
    }
    RowLayout {
        visible: root.state === "recovery-needed"
        spacing: 12
        Ui.Button {
            objectName: "recoverButton"
            text: "Recover…"
            background: Tone.accent; foreground: Tone.bg
            enabled: !root.busy
            onClicked: root.request({op: "begin_recover"})
        }
        PanelText { text: "You confirm the steps first."; alpha: 0.55; font.pixelSize: 11 }
    }

    Note {
        visible: root.state === "external-run"
        tone: "quiet"
        title: "Already ended?"
        text: "Then it left a journal behind. Recover it from a terminal with the consent file it was started with."
    }
    CodeBox { objectName: "commandBox"; visible: root.state === "cli-recovery"; text: root.command }
    CodeBox { objectName: "journalBox"; visible: root.state === "external-run" && root.journal !== ""; text: root.journal }
    RowLayout {
        visible: root.state === "cli-recovery" || root.state === "external-run"
        spacing: 8
        Ui.Button { objectName: "copyCommand"; visible: root.state === "cli-recovery"; text: "Copy command"; bordered: true; onClicked: root.copy(root.command) }
        Ui.Button { objectName: "copyJournal"; visible: root.journal !== ""; text: "Copy journal path"; bordered: true; onClicked: root.copy(root.journal) }
    }
    PanelText {
        visible: root.state === "cli-recovery" || root.state === "external-run"
        text: "Full steps: docs/technical.md, “CLI recovery”."
        alpha: 0.55; font.pixelSize: 11
    }
    Item { Layout.fillHeight: true }

    footer: PanelText {
        alpha: 0.55; font.pixelSize: 11
        text: root.state === "recovery-needed" ? "Start is available again once recovery completes."
            : root.state === "cli-recovery" ? "This page updates by itself when the journals are cleared."
            : "Start, Stop and Recover stay closed until it is gone."
    }
}
