# Luma Ultra connection observations

User confirmed Luma Ultras connected for read-only inspection. Baseline had only DP-1 Samsung display and no VITURE USB matches. Collection timestamp is in adjacent host-report.json.

Observed USB product `VITURE Luma Ultra XR GLASSES`, vendor/product `35ca:1104`, authorized; additional VITURE Microphone `35ca:1102`.
New physical compositor output DP-2, make CVT, model VITURE; active 1920x1080 at compositor-reported 60.0 Hz. Pair association is supported by user-confirmed one-pair hotplug, matching USB product and newly appearing VITURE output, not a universal USB/display mapping algorithm.

Supplemental read of explicit selected-session `hyprctl -i INSTANCE monitors all -j` retained only display fields. Raw availableModes for DP-2:
- 1920x1080@60.00Hz
- 1920x1200@120.00Hz
- 1920x1200@90.00Hz
- 1920x1200@60.00Hz
- 1920x1080@90.00Hz

Important: 120 Hz is advertised at 1920x1200, not at 1920x1080 in this observed mode list. This is compositor-advertised evidence, not an active 120 Hz mode or measured presentation. Preserve exact mode tokens. No mode changes performed. Inspect SDK/native-vs-stereo/firmware behavior before choosing the renderer's output mode.

Connected DRM connectors: card1-DP-1 and card1-DP-2, both device symlink resolving to /sys/devices/pci0000:00/0000:00:01.1/0000:01:00.0/0000:02:00.0/0000:03:00.0/drm/card1. PCI address 03:00.0 previously identified as AMD Navi 32; actual XR rendering/import behavior still untested.

Current diagnostic schema leaves available_modes unknown because its collector drops availableModes. The supplemental observation above closes the read-only evidence gap for this pair without pretending the CLI already normalizes that field. Backend plan must implement/test this conversion or keep evidence separate.

No firmware read, SDK initialization, head tracking, sustained frame-rate test, mode switch, desktop edits or screen capture performed.
