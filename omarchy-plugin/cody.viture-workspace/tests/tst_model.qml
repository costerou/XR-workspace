import QtQuick
import QtTest
import "../Model.js" as Model

TestCase {
    name: "WorkspaceModel"
    function test_scales() {
        compare(Model.validScales(1920, 1080), [1, 1.25, 1.5, 2])
        compare(Model.validScales(3440, 1440), [1, 1.25, 2])
        compare(Model.validScales(5120, 1440), [1, 1.25, 2])
        compare(Model.validScales(3840, 1080), [1, 1.25, 1.5, 2])
        compare(Model.validScales(0, 1080), [])
        compare(Model.validScales(true, 1080), [])
    }
    function test_presets() {
        compare(Model.presetsFor("16:9"), [{width:1920,height:1080}, {width:2560,height:1440}])
        compare(Model.presetsFor("21:9"), [{width:2560,height:1080}, {width:3440,height:1440}])
        compare(Model.presetsFor("32:9"), [{width:3840,height:1080}, {width:5120,height:1440}])
        compare(Model.presetsFor("custom"), [])
        let copy = Model.presetsFor("16:9")
        copy[0].width = 1
        compare(Model.presetsFor("16:9")[0].width, 1920)
    }
    function test_shapes() {
        compare(Model.shapeOf(3440, 1440), "21:9")
        compare(Model.shapeOf(2560, 1080), "21:9")
        compare(Model.shapeOf(3840, 1080), "32:9")
        compare(Model.shapeOf(2560, 1440), "16:9")
        compare(Model.shapeOf(1000, 800), "custom")
    }
    function test_preview() {
        const desktops = [{width:1920,height:1080}, {width:3840,height:1080}, {width:3440,height:1440}]
        const rects = Model.previewRects(desktops, 800, 400, 20)
        compare(rects.length, 3)
        for (let i = 0; i < 3; ++i) {
            fuzzyCompare(rects[i].height, rects[0].height, 0.001)
            fuzzyCompare(rects[i].width / rects[i].height, desktops[i].width / desktops[i].height, 0.001)
            verify(rects[i].x >= 0 && rects[i].y >= 0)
            verify(rects[i].x + rects[i].width <= 800.001)
            if (i) fuzzyCompare(rects[i].x - rects[i-1].x - rects[i-1].width, 20, 0.001)
        }
        const small = Model.previewRects([{width:1920,height:1080}], 800, 100, 20)[0]
        compare(small.height, 100)
        fuzzyCompare(small.x + small.width / 2, 400, 0.001)
        compare(Model.previewRects([], 800, 400, 20), [])
        compare(Model.previewRects(desktops, 10, 400, 20), [])
    }
    function test_colors() {
        compare(Model.barColorRole("running"), "accent")
        for (const state of ["recovery-needed", "cli-recovery", "failed"])
            compare(Model.barColorRole(state), "warning")
        for (const state of ["idle", "no-glasses", "confirming", "starting", "stopping", "unknown"])
            compare(Model.barColorRole(state), "muted")
    }
    function test_warning() {
        verify(Model.largeLayoutWarning([{width:1920,height:1080}, {width:5120,height:1440}, {width:1920,height:1080}]))
        verify(!Model.largeLayoutWarning(Array(3).fill({width:1920,height:1080})))
    }
}
