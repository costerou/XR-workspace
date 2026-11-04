Offline mode-switch evidence (2026-11-04)

The initial classification test failed before implementation (red.txt).
`python -m tools.mode_switch_gap` measures a fake one-second DP outage using
survive.wait_for_glasses and a fake clock. Before: restart ready at 4.0s.
After: pending-switch restart ready at 1.0s. Ordinary loss remains 4.0s.
This measures supervisor delay only, excluding renderer startup and hardware
blackout. It is not a live measurement of time to picture.

Implementation uses a fast renderer restart in the existing session. The
Wayland layer surface is tied to the removed output and currently closes on
removal. Desktop placements and the tracking driver survive; renderer-local
pose/recenter state is not transferred and remains a limitation.

The panel shows a switching card while the switch is pending. No software
card can reach the glasses while their display link is disconnected.

A pending switch gives the display eight seconds from the first observed
outage to return in a supported new mode (3840x1200 at 60/90, or 1920x1200 at
120). A confirmed transition logs mode-switch and resets the expected mode.
An outage exceeding the window still reports video-lost; USB and driver
fault detectors remain active. Exit expectation is bounded rather than
lasting forever if the user never presses R1.

R1 without clicking 3D: a one-second monitor probe offers desktop depth in a
toast when the glasses become wide. It does not start the AI engine. This is
the conservative choice because R1 can also be intended for SBS video.

No installation or hardware/session manipulation was performed.
