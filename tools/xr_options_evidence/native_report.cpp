// CPU-only production serializer seam. No VideoMode/player, context or device is created.
#include "video_mode.hpp"
#include <iostream>
int main() {
    xr::VideoDefaults defaults;
    defaults.set({xr::VideoFilter::sharp, true});
    std::cout << defaults.report() << '\n';
}
