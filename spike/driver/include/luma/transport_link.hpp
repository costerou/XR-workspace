#pragma once
// luma::Transport as the daemon's Link. Templated on the transport and its status enum so tests/daemon_test.cpp can run it
// against a stand-in of the same shape; wiring.cpp uses TransportLink<Transport, IoStatus>.
//
// Transport reconnects inside read() and reports Connected, while the daemon wants to decide when a link is open, so:
//   open()             starts it (first time), and is true once Connected has been seen and not been followed by a loss
//   wait_for_device()  is read() while down: that is where Transport's sysfs rescan runs, and where Connected arrives
// Transport never sees a libudev monitor: uevents do not reach the driver's network namespace (see transport.hpp).
//
// The glasses talk on more than one endpoint. Set a stream endpoint and the daemon's read() sees only that one (and a second,
// add_stream_endpoint: the display events); everything else (the MCU's reply to the start, say) goes to the other-packet
// handler, on the same thread.
#include "luma/daemon.hpp"
#include <functional>

namespace luma {
template <class TransportT, class Io>
class TransportLink final : public Link {
public:
    template <class... Args> explicit TransportLink(Args&&... args) : transport_(std::forward<Args>(args)...) {}
    bool open(std::string& why) override {
        if (!started_) { started_ = true; transport_.open(); }
        if (!link_up_) pump(milliseconds(0));
        if (!link_up_) why = transport_.last_error();
        return link_up_;
    }
    ReadResult read(std::span<std::uint8_t> into, milliseconds timeout) override {
        const auto r = transport_.read(into, timeout);
        switch (r.status) {
        case Io::Ok:
            if constexpr (requires { r.endpoint; }) {
                if (stream_endpoint_ && r.endpoint != stream_endpoint_ && r.endpoint != second_stream_endpoint_) {
                    if (other_packet_) other_packet_(r.endpoint, into.first(std::min(r.bytes, into.size())));
                    return {ReadStatus::timeout, 0};
                }
            }
            if constexpr (requires { r.t_ns; }) return {ReadStatus::data, r.bytes, r.t_ns};
            else return {ReadStatus::data, r.bytes};
        case Io::Disconnected: case Io::Closed: link_up_ = false; return {ReadStatus::gone, 0};
        default: return {ReadStatus::timeout, 0};  // Timeout, Interrupted, and a Connected that needs nothing from us
        }
    }
    bool write(std::span<const std::uint8_t> bytes) override {
        if constexpr (requires { transport_.write_to(std::uint8_t{}, bytes, kWriteTimeout); })
            if (route_) return transport_.write_to(route_(bytes), bytes, kWriteTimeout) == Io::Ok;
        return transport_.write(bytes, kWriteTimeout) == Io::Ok;
    }
    void close() override {
        // A silent device: let go of it entirely. A gone one: Transport is already looking for it, so keep that going.
        if (link_up_) { transport_.close(); started_ = link_up_ = false; }
    }
    bool wait_for_device(milliseconds timeout) override { return pump(timeout); }
    TransportT& transport() { return transport_; }
    void set_stream_endpoint(std::uint8_t endpoint) { stream_endpoint_ = endpoint; }
    void add_stream_endpoint(std::uint8_t endpoint) { second_stream_endpoint_ = endpoint; }
    void set_other_packet_handler(std::function<void(std::uint8_t, std::span<const std::uint8_t>)> handler) { other_packet_ = std::move(handler); }
    void set_route(std::function<std::uint8_t(std::span<const std::uint8_t>)> route) { route_ = std::move(route); }
private:
    static constexpr milliseconds kWriteTimeout{200};  // one short command; the pose thread must not sit in it for long
    bool pump(milliseconds timeout) {
        std::uint8_t discard[1024];  // nothing arrives while down; a packet after Connected is one sample lost at most
        const auto r = transport_.read(discard, timeout);
        if (r.status == Io::Connected || (r.status == Io::Ok && !link_up_)) link_up_ = true;
        else if (r.status == Io::Disconnected || r.status == Io::Closed) link_up_ = false;
        return link_up_;
    }
    TransportT transport_;
    bool started_ = false, link_up_ = false;
    std::uint8_t stream_endpoint_ = 0, second_stream_endpoint_ = 0;
    std::function<void(std::uint8_t, std::span<const std::uint8_t>)> other_packet_;
    std::function<std::uint8_t(std::span<const std::uint8_t>)> route_;
};
}
