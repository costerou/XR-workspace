#pragma once
// GLES 3 drawing of a decoded VR video frame (video.hpp): a full-viewport pass that re-projects every output pixel from
// the frame with the head rotation. Needs the compositor's EGL context current for create(), draw() and destruction.
#include "xr/video.hpp"
#include <cstdint>
#include <memory>
#include <string>

namespace xr {
// How the projection pass reconstructs the frame where it is magnified (the usual case: the Luma shows ~48 px per
// degree, an 8K 360° frame has ~21). Minified areas (equirect poles, fisheye rims) always use trilinear + anisotropic
// mipmaps. The filter is a quality setting (VideoFilter in video.hpp, the Q menu's texture filter); VideoMode sets it
// per frame. XR_VIDEO_FILTER=bilinear|catmull-rom|sharp|nearest in the environment forces one for A/B checks.
bool video_filter_parse(const char* name, VideoFilter& out);  // false for nullptr/unknown; "linear" = bilinear
VideoFilter video_filter_from(const char* name);  // nullptr/unknown: catmull_rom
const char* video_filter_name(VideoFilter f);
float video_filter_cubic_a(VideoFilter f);         // Keys cubic parameter: 0 = bilinear/nearest, -0.5 Catmull-Rom, -0.75 sharper

// Video texture sampling the projection pass expects: a full mip chain (regenerated per video frame), trilinear,
// anisotropic where the GPU has it, clamped. Levels for a w x h texture:
std::uint32_t video_texture_levels(std::uint32_t w, std::uint32_t h);
// Sets filtering on the texture bound to GL_TEXTURE_2D; returns the anisotropy applied (1 without the extension).
float video_texture_filtering();

class VideoSphere {
public:
    static std::unique_ptr<VideoSphere> create(std::string& why);  // nullptr (with `why`) if the shader fails
    ~VideoSphere();
    // Draw `eye` of the frame in `texture` (RGBA; row 0 = top of the picture, or the bottom with `flip_y`) into the
    // current viewport, seen through tan(half FOV) `half_w`/`half_h` with the world -> eye rotation `view`. Pixels
    // outside the picture are black, or with `behind` left as they are (a background was drawn there first). Blending off;
    // overwrites the viewport.
    void draw(unsigned texture, bool flip_y, const VideoFormat& format, int eye, const Mat3& view, float half_w, float half_h,
              bool behind = false);
    void set_filter(VideoFilter f) { filter_ = f; }
    VideoFilter filter() const { return filter_; }
    bool forced() const { return forced_; }  // XR_VIDEO_FILTER named a filter: it wins over the quality setting
private:
    VideoSphere() = default;
    unsigned program_ = 0;
    int pos_ = -1, tex_ = -1, view_ = -1, half_ = -1, projection_ = -1, rect_ = -1, fov_ = -1, flip_ = -1, cubic_ = -1, behind_ = -1;
    VideoFilter filter_ = VideoFilter::catmull_rom;
    bool forced_ = false;
};
}
