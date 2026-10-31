#pragma once
// GBM allocation of capture targets on the compositor's advertised render node (C2b).
// Only /dev/dri render nodes are opened; a primary node is mapped to its render node via sysfs.
#include "xr/capture.hpp"
#include <array>
#include <cstdint>
#include <memory>
#include <string>

struct gbm_device;
struct gbm_bo;

namespace xr {
struct DmabufBuffer {
    gbm_bo* bo = nullptr;
    int planes = 0;
    std::array<int, 4> fds{-1, -1, -1, -1};
    std::array<std::uint32_t, 4> strides{}, offsets{};
    std::uint64_t modifier = 0;
    std::uint32_t fourcc = 0, width = 0, height = 0;
    ~DmabufBuffer();
};
class DmabufAllocator {
public:
    static std::unique_ptr<DmabufAllocator> open(std::uint64_t device, std::string& why);
    ~DmabufAllocator();
    std::unique_ptr<DmabufBuffer> allocate(std::uint32_t width, std::uint32_t height, DmabufChoice choice, std::string& why);
    std::string node() const { return node_; }
private:
    DmabufAllocator() = default;
    int fd_ = -1;
    gbm_device* gbm_ = nullptr;
    std::string node_;
};
}
