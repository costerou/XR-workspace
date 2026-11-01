# Luma Ultra presentation-only live test

Build 3e9ea09ab6316d5eb19cb4c1077331853082410c. User connected Luma Ultras in response to request to repeat presentation/shutdown/crash test. USB readback35ca:1104, Beast absent. Physical session efb50993780079460b0cbed1363e2166a2de1d9f_1793311125_1142154332. DP-2 target, DP-1 independent recovery desktop; both mirrorOf none, DPMS on.

Compositor reported Luma1920x1080@120 active, scale1.25; advertised modes1920x1080@60,1920x1200@120/90/60,1920x1080@90. Active/advertised mismatch unresolved, no deliberate mode switch, no sustained render cadence claim.

Consent-gated present launched supervisor1013115 (proc_6edc972bf9e1). Layers readback confirmed xr-workspace-spike-test-pattern only on DP-2. User saw pattern clearly, reported it covered Omarchy bar in glasses, and confirmed regular monitor unaffected. Overlay/bar interaction observed, not a configurable feature yet.

Normal Ctrl-C shutdown: subsequent test_surfaces empty and supervisor absent.

Controlled crash repeat supervisor1013785 (proc_75d960816259): pattern verified on DP-2; renderer1013837 identified by exact executable and parent. SIGKILL sent only to that child. After two seconds layers showed no pattern, supervisor and all recorded children absent.

No capture, tracking, SDK, input monitoring, output/workspace/configuration mutation or refresh switch. Post-crash user reports normal desktop returned BUT Omarchy bar shifted downward and distorted background above it. Live visual cleanup acceptance FAILED/pending investigation, despite absent renderer processes/surface. Subsequent read-only layers show only Omarchy surfaces; DP-2 bar surface x4096,y0,w1536,h864, reserved[0,26,0,0], mode1920x1080@120 scale1.25 unchanged from preflight. No cause established; no corrective desktop changes performed. Managed desktops, focus routing, head tracking and performance remain unqualified.

## Display timing diagnosis and authorized runtime correction

User authorized capturing only DP-2. grim capture showed correctly positioned bar without distortion, while user still saw distortion in glasses. Disconnect/reconnect did not resolve it. User then authorized temporary DP-2 mode tests, no saved configuration edits. Legacy `hyprctl keyword monitor` was rejected by the Lua parser and made no change; selected-instance `hyprctl eval` with `hl.monitor` applied runtime-only changes.

1920x1080@60 at4096x0 scale1.25: compositor readback60.0 Hz; user confirmed distorted strip disappeared and bar returned to correct position.
1920x1200@120 at4096x0 scale1.25: compositor readback1920x1200 at119.999 Hz; user confirmed correct image, no strip and properly positioned bar. DP-1 remained5120x1440@240 scale1.25 throughout. Current DP-2 runtime mode is the latter; configuration files unchanged.

Evidence strongly implicates the prior unadvertised1080p120 timing. Supported1200p120 is visually usable in this test; this is NOT measurement of120fps rendering, motion-to-photon latency or tracking. Corrected-timing retest completed: supervisor1019988 showed the pattern only on DP-2 at1920x1200/119.999Hz; user confirmed clean image. Normal Ctrl-C removed the surface. Repeat supervisor1020302, verified exact child renderer1020354, controlled SIGKILL; subsequent layers empty and all recorded processes absent. User confirmed after cleanup: desktop clean, bar correctly positioned, no distorted strip. Basic presentation and normal/crash visual cleanup now pass at the corrected timing, not sustained performance or tracking qualification.
