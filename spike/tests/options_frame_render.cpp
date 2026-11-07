// CPU only: draws real `options_menu` wire frames with the production panel code, and the Maximize / Restore icons from the
// production atlas magnified 8x. No compositor, GL context, device or service opens.
//
// usage: options_frame_render OUT_DIR < frames   (writes menu-N.ppm per frame, icons.ppm)
#include "xr/glass.hpp"
#include "xr/ipc.hpp"
#include <algorithm>
#include <fstream>
#include <iostream>
#include <iterator>
#include <string>
#include <vector>
using namespace xr;
namespace {
void write(const std::string& path, const HudImage& im) {
    std::ofstream out(path, std::ios::binary);
    out << "P6\n" << im.width << ' ' << im.height << "\n255\n";
    for (std::size_t p = 0; p < im.rgba.size(); p += 4) {  // premultiplied, over the overview's black
        out.write(reinterpret_cast<const char*>(&im.rgba[p]), 3);
    }
}
}
int main(int argc, char** argv) {
    if (argc != 2) { std::cerr << "usage: options_frame_render OUT_DIR < frames\n"; return 2; }
    std::string why;
    auto font = HudFont::load(kHudFontFamily, why);
    std::vector<OptionsMenuState> menus;
    ControlStream stream("run", "session", false, [&](ControlMessage& m) {
        if (const auto* menu = std::get_if<OptionsMenuState>(&m.payload)) menus.push_back(*menu);
    }, [](const ControlRefusal& r) { std::cerr << "refused: " << r.error.detail << '\n'; });
    stream.feed(std::string{std::istreambuf_iterator<char>(std::cin), std::istreambuf_iterator<char>()});
    for (std::size_t i = 0; i < menus.size(); ++i) write(std::string(argv[1]) + "/menu-" + std::to_string(i) + ".ppm", render_options_panel(menus[i], font.get()));
    const auto atlas = render_handle_atlas({.45f, .65f, .8f}, font.get());
    const int cell = 78, zoom = 8, gap = 16, boxes = 2;
    HudImage sheet{std::uint32_t((cell * zoom + gap) * boxes + gap), std::uint32_t(cell * zoom + 2 * gap), {}};
    sheet.rgba.assign(std::size_t(sheet.width) * sheet.height * 4, 0);
    for (int b = 0; b < boxes; ++b) {
        const auto box = b == 1 ? options_restore_box(false) : options_icon_box(false);
        const int x0 = int(box.u0 * float(atlas.width) + .5f), y0 = int(box.v0 * float(atlas.height) + .5f);
        for (int y = 0; y < cell * zoom; ++y)
            for (int x = 0; x < cell * zoom; ++x) {
                const auto* p = &atlas.rgba[(std::size_t(y0 + y / zoom) * atlas.width + std::size_t(x0 + x / zoom)) * 4];
                auto* q = &sheet.rgba[(std::size_t(gap + y) * sheet.width + std::size_t(gap + (cell * zoom + gap) * b + x)) * 4];
                std::copy_n(p, 4, q);
                // The cell's centre lines, so what is off centre shows.
                if (x == cell * zoom / 2 || y == cell * zoom / 2) { q[0] = 255; q[1] = 80; q[2] = 80; }
            }
    }
    write(std::string(argv[1]) + "/icons.ppm", sheet);
    std::cout << menus.size() << " menus\n";
}
