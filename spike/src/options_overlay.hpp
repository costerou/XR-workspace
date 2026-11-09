#pragma once
#include "xr/glass.hpp"
#include "xr/ipc.hpp"

namespace xr {
// Options keep their input geometry fixed. The last visible snapshot and texture survive a close for the fade,
// while the supervisor's logical state immediately stops accepting input. Updates rasterize once per drawn frame.
class OptionsOverlay {
public:
    bool set(const OptionsMenuState& state, double now_ms) {
        bool changed = fade_.set(state.open, now_ms);
        if (state.open && (!generation_ || state != state_)) {
            state_ = state; dirty_ = true; ++generation_; changed = true;
        }
        return changed;
    }
    void set_font(std::unique_ptr<HudFont> font) { font_ = std::move(font); dirty_ = generation_ != 0; }
    bool set_accent(std::array<float, 3> accent) {
        if (accent == accent_) return false;
        accent_ = accent;
        if (!generation_) return false;
        dirty_ = true; ++generation_; return true;
    }
    float frame(bool overview, double now_ms) {
        if (!overview) {
            if (hidden_ms_ >= 0 && fade_.visible()) fade_.hold(hidden_ms_, now_ms);
            hidden_ms_ = now_ms;
            return 0.f;
        }
        if (hidden_ms_ >= 0 && fade_.visible()) fade_.hold(hidden_ms_, now_ms);
        hidden_ms_ = -1;
        return generation_ ? fade_.alpha(now_ms) : 0.f;
    }
    bool needs_frame(bool overview, bool was_drawn, double now_ms) const {
        return (overview && fade_.animating(now_ms)) || (was_drawn && (!overview || !fade_.visible()));
    }
    const OptionsMenuState& state() const { return state_; }
    const HudImage& image() const {
        if (dirty_) { image_ = render_options_panel(state_, font_.get(), accent_); dirty_ = false; ++renders_; }
        return image_;
    }
    std::uint64_t generation() const { return 1000000 + generation_; }
    std::uint64_t renders() const { return renders_; }
private:
    CardFade fade_{kMenuFadeInMs, kMenuFadeOutMs};
    OptionsMenuState state_;
    std::unique_ptr<HudFont> font_;
    std::array<float, 3> accent_ = kGlassThemeDefault;
    double hidden_ms_ = -1;
    std::uint64_t generation_ = 0;
    mutable std::uint64_t renders_ = 0;
    mutable bool dirty_ = false;
    mutable HudImage image_;
};
}
