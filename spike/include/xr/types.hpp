#pragma once
#include <cmath>
#include <cstdint>
#include <optional>
#include <span>
#include <string>
#include <variant>

namespace xr {
using MonoNs = std::int64_t;
struct Quaternion { double w, x, y, z; };
using OutputId = std::string; // Caller-owned stable UUID, never an array index.
struct PoseSample {
    Quaternion orientation;
    std::uint64_t epoch, sequence;
    std::optional<std::uint64_t> device_tick;
    std::optional<MonoNs> sample_mono_ns;
    MonoNs received_ns;
    bool valid;
};
struct OutputSpec {
    OutputId id;
    std::string name;
    std::uint32_t width, height, refresh_millihz;
    double scale;
    std::int32_t x, y;
};
struct FrameToken {
    OutputId output;
    std::uint64_t generation, sequence;
    std::uint32_t slot;
    MonoNs ready_received_ns;
};
struct Activity {
    MonoNs received_ns;
    bool key_activity, pointer_motion, drag_held, reliable;
};
struct Target { OutputId output; std::optional<std::string> window_address; };
struct FocusDecision { std::optional<Target> target; std::string reason; };
struct Error { std::string code, detail; };
using Status = std::variant<std::monostate, Error>;
template<class T> using Result = std::variant<T, Error>;

// Read-only validation: IDs are supplied by the owner and never reassigned.
inline Status validate_layout(std::span<const OutputSpec> outputs) {
    for (std::size_t i = 0; i < outputs.size(); ++i) {
        const auto& output = outputs[i];
        if (output.id.empty())
            return Error{"invalid_id", "Output ID must be nonempty"};
        for (std::size_t j = 0; j < i; ++j)
            if (outputs[j].id == output.id)
                return Error{"duplicate_id", "Output IDs must be unique"};
        if (output.width == 0 || output.height == 0 || output.refresh_millihz == 0)
            return Error{"invalid_mode", "Dimensions and refresh must be positive"};
        if (!std::isfinite(output.scale) || output.scale <= 0)
            return Error{"invalid_scale", "Scale must be positive and finite"};
    }
    return {};
}
} // namespace xr
