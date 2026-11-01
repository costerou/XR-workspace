# Beast presentation-only live test

Build: 3e9ea09ab6316d5eb19cb4c1077331853082410c.

User authorized test pattern, normal shutdown and controlled renderer crash, confirmed Beast connected. USB readback: 35ca:1201 VITURE Beast XR Glasses. Selected physical Hyprland instance efb50993780079460b0cbed1363e2166a2de1d9f_1793311125_1142154332. Target DP-2; independent recovery DP-1. Both mirrorOf none, DPMS on. DP-2 reported active 1920x1080 at 120 Hz, scale1.25; availableModes instead listed 60/59.94 Hz. This discrepancy is unresolved and no sustained frame cadence is claimed.

Executed consent-gated `python -m tools.native_spike present` with private presentation-only consent. Hyprland layers readback showed xr-workspace-spike-test-pattern on DP-2 only. User confirmed visible pattern and normal regular-monitor use: 'Everything is working normally.' This is manual usability evidence, not exhaustive keyboard focus testing.

Normal shutdown: Ctrl-C to supervisor session proc_e31cbd0c78a1. Subsequent layers readback: test_surfaces []; ps confirmed supervisor1007399 and child processes1007451/1007452/1007453 absent.

Controlled crash: second supervised launch proc_7df2bca857fe, supervisor1008133. Before termination, layers readback showed pattern on DP-2. Verified child executable path and parent relationship, sent SIGKILL to renderer1008185 only. After two seconds: no test surfaces; supervisor and recorded children all absent. No global process-name kill used.

No output creation/removal/configuration, workspace moves, refresh-rate switch, capture, tracking, input monitoring or SDK execution. Live display pattern and teardown exercised successfully; three-desktop lifecycle, tracking/follow, 120 Hz rendering performance and Luma qualification remain untested. User confirmed after crash: Beast returned to its normal desktop view and regular monitor still worked normally.
