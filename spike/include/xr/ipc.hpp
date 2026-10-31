#pragma once
#include "xr/types.hpp"
#include <deque>
#include <string_view>

namespace xr {
enum class ControlOp { status, start, stop, select, recover, view };
struct SelectPayload { OutputId output_id; };
// Glasses view: overview vs zoomed into `selected` (0..2), gap `spacing` px (0..400).
struct ViewPayload { bool zoomed = false; std::uint32_t selected = 0, spacing = 0; };
using ControlPayload = std::variant<std::monostate, SelectPayload, ViewPayload>;
struct ControlMessage {
    std::uint32_t schema;
    std::uint64_t request_id;
    std::string run_id, session;
    ControlOp op;
    ControlPayload payload;
};
Result<ControlMessage> decode_control(std::string_view frame);
class ControlQueue {
public:
    ControlQueue(std::string run_id, std::string session);
    Status push(std::string_view frame);
    std::optional<ControlMessage> pop();
private:
    std::string run_id_, session_;
    std::uint64_t last_ = 0;
    std::deque<ControlMessage> messages_;
};
} // namespace xr
