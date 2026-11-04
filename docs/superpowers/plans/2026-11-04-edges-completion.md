# Stereo edge completion implementation plan

Goal: finish Cody's original edge and strength brief with reproducible offline evidence and green suites.
Architecture: retain the real engine/shared-memory/headless compositor chain, compare origin/master 00c2328 with the completed branch at identical settings. Make hole fill choose verified far-side samples and feather its boundary. Measure picture-space edge residuals and still-region temporal flicker alongside stereo_eval comfort measures.
Constraints: no subagents; no installation or merge; no running XR/driver/engine or desktop configuration changes; GPU at nice 19. User explicitly requests continuous execution; no design approval pause.

- [x] Trace each Python failure. Existing failing tests are regression cases. Isolate fake-driver discovery from host drivers in tests; retain foreign-driver conflict coverage. Fix the worker failure/request race, not its assertions. Re-run directional-switch and timing tests in isolation; use CPU time for CPU performance assertions if descheduling is the cause.
- [x] Add known-answer edge/temporal metric tests and a CPU fill regression with a coloured foreground and textured background. Watch fail, implement and compare GPU mirror.
- [x] Extend offline proof tooling for five required scenes, before/after runs and detailed recordings. Explicit offline-active-session option is authorized by user's clarification; never contact XR. Save SBS, anaglyph and edge crops plus clips under edges-*.
- [x] Benchmark before/after with real engine, 1–4 desktops and maps/s. Inspect images and metric tradeoffs; iterate at the source.
- [x] Run each Python suite, QML, bind_check and all CTests on iGPU. Record counts, review diff, commit and normal push.

Review focus: foreground colour bleeding into holes; static textures mistaken for text; moving edges dragging history; scene cut resetting both filters; per-desktop override persistence and live updates.

Final review: performed inline because the user prohibits subagents. Found and fixed the still-desktop strength refresh gap with an iGPU regression test. All acceptance work is complete; see docs/3d-edges-strength.md for evidence. The noisy-photo torture proxy has a small recorded regression and is explicitly reported.
