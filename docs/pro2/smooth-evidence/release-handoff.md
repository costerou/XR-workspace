# Supervised lean check release

Runtime source `ef596e69fb396b196cdb107b21373071a5eea894`, which contains master `0b43593`. Later commits are documentation only. It replaces `87584f1`, the bundle of Run B, kept for that run's record with `28d20ca`.

Release: `~/.local/state/viture-workspace/pro2-hil/bundles/ef596e6/pro2-runtime-release`

Archive: `pro2-runtime-release-ef596e6.tar.gz` beside it, SHA256 `d555f5357de5637397deda3a18005609d06b5ad8c3e25a17accb4a060d45c704`

Manifest SHA256 `1f7854cd5291760de13aa453de738e1c567d2a801ed487a2ed14dd561d6620fc`, 371 runtime files. Default mount pitch -11, provisional.

| Artifact | SHA256 |
| --- | --- |
| pro2-sdk-bridge | edaee50978a69d29ffb4d0ad244dc1827c9f4350b3539f1692471a80acbe8d3b |
| xr-native-spike | 8e6a0faf2e251448326f986ffcf65432f8c5d91c1d9ead0203de1820817db2e8 |
| libxr-ownership.so | 9587ca186ac1de176c8c30ee4b5862bf377318ce18af9cbb7fffec4f33bfb5bd |

Checked on a fresh extraction of that archive, under `/usr/bin/python3`: SHA256SUMS, 371 pins, preflight with no SDK and no compositor, and the genuine CLI matrix, 5 of 5.

| Suite | Result |
| --- | --- |
| Genuine CLI matrix, fresh extraction | 5 passed |
| tests/pro2_runtime | 54 tests, 7 skipped |
| tests/pro2_runtime_qa | 42 tests, 2 skipped |
| tests/pro2_smooth_qa (independent review) | 55 tests, 1 expected failure: too few distinct stops in the captures |
| smooth acceptance with built binaries | 17 passed |
| tests/xr_controller | 600 passed |
| tests/native_spike | 2559 run, 22 skipped; test_stabilization needs pytest, see below |
| xr_options_integration, stabilization, native test_stabilization (pytest venv) | 356 passed, 3 skipped |
| tests/pro2, pro2_diagnostic, xr_options_acceptance | 8, 29 (2 skipped), 6 |
| QML, offscreen | 49 passed |
| ctest, no-GPU subset, `nice -n 19`, private namespaces | 70 of 70 |

**Not run on this source:** the 44 GPU ctests. A normal XR session was live (`viture-xr-5a2255e7...`), and the rule then is the no-GPU subset. All 114 last passed at `87584f1`. Renderer changes since: an argument in `main.cpp`, telemetry and an arguments change in `wayland.cpp`, and gravity levelling off by default. With XR down, and that checked on its own first:

    unshare -Ur -i -n -- env DRI_PRIME=pci-0000_12_00_0 XR_BENCH_RENDER_NODE=/dev/dri/renderD129 XR_CAPTURE_TEST_NODE=/dev/dri/renderD129 ctest --test-dir BUILD -j 1 --output-on-failure --timeout 300

Nothing here was run in the glasses, on the SDK, or on a live compositor. The steps for that are in [../smooth-live-test.md](../smooth-live-test.md).
