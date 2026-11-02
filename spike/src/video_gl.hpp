#pragma once
// GLES2 drawing of a decoded VR video frame (video.hpp): a full-viewport pass that re-projects every output pixel from
// the frame with the head rotation. Needs the compositor's EGL context current for create(), draw() and destruction.
#include "xr/video.hpp"
#include <memory>
#include <string>

namespace xr {
class VideoSphere {
public:
    static std::unique_ptr<VideoSphere> create(std::string& why);  // nullptr (with `why`) if the shader fails
    ~VideoSphere();
    // Draw `eye` of the frame in `texture` (RGBA; row 0 = top of the picture, or the bottom with `flip_y`) into the
    // current viewport, seen through tan(half FOV) `half_w`/`half_h` with the world -> eye rotation `view`. Pixels
    // outside the picture are black. Blending off; overwrites the viewport.
    void draw(unsigned texture, bool flip_y, const VideoFormat& format, int eye, const Mat3& view, float half_w, float half_h);
private:
    VideoSphere() = default;
    unsigned program_ = 0;
    int pos_ = -1, tex_ = -1, view_ = -1, half_ = -1, projection_ = -1, rect_ = -1, fov_ = -1, flip_ = -1;
};
}
