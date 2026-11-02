#pragma once
// `video` control payload decoder (ipc.hpp VideoPayload); kept apart from ipc.cpp's other payloads.
#include "xr/ipc.hpp"
#include <nlohmann/json.hpp>

namespace xr {
bool decode_video_payload(const nlohmann::json& payload, VideoPayload& out);
}
