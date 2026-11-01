#include "dmabuf.hpp"
#include <gbm.h>
#include <dirent.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <unistd.h>
#include <cstring>

namespace xr {
DmabufBuffer::~DmabufBuffer() {
    for (int fd : fds) if (fd >= 0) ::close(fd);
    if (bo) gbm_bo_destroy(bo);
}

namespace {
// Render node for a DRM device number: itself if it is a render node, else via sysfs.
std::string render_node(std::uint64_t device) {
    const dev_t dev = static_cast<dev_t>(device);
    const std::string sys = "/sys/dev/char/" + std::to_string(major(dev)) + ":" + std::to_string(minor(dev)) + "/device/drm";
    if (DIR* d = opendir(sys.c_str())) {
        std::string found;
        while (dirent* e = readdir(d)) if (std::strncmp(e->d_name, "renderD", 7) == 0) { found = std::string("/dev/dri/") + e->d_name; break; }
        closedir(d);
        struct stat st{};
        if (!found.empty() && stat(found.c_str(), &st) == 0 && S_ISCHR(st.st_mode)) return found;
    }
    return {};
}
}

std::unique_ptr<DmabufAllocator> DmabufAllocator::open(std::uint64_t device, std::string& why) {
    std::unique_ptr<DmabufAllocator> a(new DmabufAllocator());
    a->node_ = render_node(device);
    if (a->node_.empty() || a->node_.rfind("/dev/dri/renderD", 0) != 0) { why = "no render node for advertised dmabuf device"; return nullptr; }
    a->fd_ = ::open(a->node_.c_str(), O_RDWR | O_CLOEXEC);
    if (a->fd_ < 0) { why = "render node open failed"; return nullptr; }
    a->gbm_ = gbm_create_device(a->fd_);
    if (!a->gbm_) { why = "gbm device failed"; return nullptr; }
    return a;
}

DmabufAllocator::~DmabufAllocator() {
    if (gbm_) gbm_device_destroy(gbm_);
    if (fd_ >= 0) ::close(fd_);
}

std::unique_ptr<DmabufBuffer> DmabufAllocator::allocate(std::uint32_t width, std::uint32_t height, DmabufChoice choice, std::string& why) {
    auto b = std::make_unique<DmabufBuffer>();
    b->width = width; b->height = height; b->fourcc = choice.fourcc;
    const std::uint32_t usage = GBM_BO_USE_RENDERING;
    b->bo = choice.modifier == kModInvalid ? gbm_bo_create(gbm_, width, height, choice.fourcc, usage)
                                           : gbm_bo_create_with_modifiers2(gbm_, width, height, choice.fourcc, &choice.modifier, 1, usage);
    if (!b->bo) { why = "gbm allocation failed"; return nullptr; }
    b->planes = gbm_bo_get_plane_count(b->bo);
    if (b->planes < 1 || b->planes > 4) { why = "unexpected plane count"; return nullptr; }
    // Implicit choice stays implicit end to end (EGL import and wl params); explicit must match.
    b->modifier = choice.modifier == kModInvalid ? kModInvalid : gbm_bo_get_modifier(b->bo);
    if (b->modifier != choice.modifier) { why = "gbm returned a different modifier"; return nullptr; }
    for (int i = 0; i < b->planes; ++i) {
        b->fds[i] = gbm_bo_get_fd_for_plane(b->bo, i);
        b->strides[i] = gbm_bo_get_stride_for_plane(b->bo, i);
        b->offsets[i] = gbm_bo_get_offset(b->bo, i);
        if (b->fds[i] < 0) { why = "dmabuf export failed"; return nullptr; }
    }
    return b;
}
}
