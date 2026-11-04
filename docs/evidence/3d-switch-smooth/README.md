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

Additional live case: 20261104T152823Z-video-lost.json, probable 3D exit.
The renderer dispatch closed at 10:27:53 ET; DRM reported DP-2 disconnected
at 10:27:54. It returned at 1920x1200@120 and the session survived. The report
also contains a tracking-lost signal at 10:27:57; the driver statistics show
no USB disconnect/reconnect. This supplements the enter case at 10:12
(1920x1200@120 -> 3840x1200@60).

`LiveModeSwitchCases` replays both directions on one continuously observed
session, with the renderer-close-before-DRM ordering, USB/driver remaining
present and three synthetic ticks with tracking unavailable. Both mode
changes must update the expected mode without a video-lost/usb-drop signal.
The precise reconnect ticks are synthetic, not a claimed live gap measurement.
The original incident did not record the 3D click, so “exit” remains probable;
the regression supplies the pending-switch intent explicitly. Independent
tracking-log signals remain active, as documented above.
The case fails against 00c2328 (live-cases-red.txt) and passes on this branch.
