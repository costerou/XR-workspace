# Beast connection observations

User confirmed Luma Ultra disconnected and Beast connected. First snapshot in ../beast/ showed no matching VITURE USB device and no second display; it is a real not-yet-enumerated attempt, not a successful Beast inventory. After user confirmed connection again, adjacent report collected at 2026-10-31T15:15:02.466226+00:00 showed:

- USB product VITURE Beast XR Glasses, vendor/product 35ca:1201, authorized.
- Additional VITURE Microphone 35ca:1102.
- Physical DP-2, make Visitech AS, model VITURE Beast.
- Active compositor-reported 1920x1080 at 120.0 Hz.
- Explicit selected-session read of hyprctl monitors all -j returned availableModes exactly: 1920x1080@120.00Hz and 1920x1080@119.88Hz.

USB/display association is based on user-confirmed one-pair hotplug, model strings and newly appearing display, not a general implemented association algorithm. Active mode is observed compositor state, not measured XR frame cadence or motion-to-photon latency. No mode changes, firmware/SDK operations or tracking tests performed.

CLI schema currently reports available_modes unknown; supplemental raw availableModes above is genuine evidence collected separately. Keep exact strings, including 119.88 Hz.
