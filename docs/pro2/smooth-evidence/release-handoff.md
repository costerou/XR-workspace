# Supervised comparison release

Runtime source `28d20ca4085f587dcbc851cb1331d3217fa23565`, which contains master `0b43593`. Later commits are documentation only.

Release: `~/.local/state/viture-workspace/pro2-hil/bundles/28d20ca/pro2-runtime-release`

Archive: `pro2-runtime-release-28d20ca.tar.gz` beside it, SHA256 `53f698cf083083ea4cedbcdf435d28c5dc7aaba5f0190649e22e9ff97833d80a`

Manifest SHA256 `6d8b1188ffaa6d6a9f4e3e9cf497992eade860cd91ca877f045fcfc97015e92a`, 364 runtime files.

| Artifact | SHA256 |
| --- | --- |
| pro2-sdk-bridge | e701973e0ae95ebccbfb47c547eff2761f8532f2ce8aad99a7ca42090170854b |
| xr-native-spike | 53449a6c57937d3f86149e34a672e10e1e3bda0d91176305a3d84ad483bf2c1a |
| libxr-ownership.so | 9587ca186ac1de176c8c30ee4b5862bf377318ce18af9cbb7fffec4f33bfb5bd |

Checked on a fresh extraction of that archive, under `/usr/bin/python3`: SHA256SUMS, 364 pins, preflight with no SDK and no compositor, and the genuine CLI matrix, 5 of 5.

| Suite | Result |
| --- | --- |
| Genuine CLI matrix, fresh extraction | 5 passed |
| tests/pro2_runtime | 33 tests, 3 skipped |
| tests/pro2_runtime_qa | 42 tests, 2 skipped |
| tests/pro2_smooth_qa (independent review) | 55 tests, 1 expected failure: too few distinct stops in the captures |
| smooth acceptance with built binaries | 11 passed |
| tests/xr_controller | 600 passed |
| tests/native_spike | 2559 run, 22 skipped; test_stabilization needs pytest, see below |
| xr_options_integration, stabilization, native test_stabilization (pytest venv) | 356 passed, 3 skipped |
| tests/pro2, pro2_diagnostic, xr_options_acceptance | 8, 29 (2 skipped), 6 |
| QML, offscreen | 49 passed |
| ctest, GPU on renderD129 only, private namespaces | 113 of 113 |

Nothing here was run in the glasses, on the SDK, or on a live compositor. The steps for that are in [../smooth-live-test.md](../smooth-live-test.md).
