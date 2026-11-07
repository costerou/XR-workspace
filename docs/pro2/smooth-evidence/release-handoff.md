# Supervised comparison release

Runtime source `87584f13a31301a43081c2fbf5eb41d1eaaea9f9`, which contains master `0b43593`. It replaces `28d20ca`, the bundle of Run A, kept for that run's record. Later commits are documentation only.

Release: `~/.local/state/viture-workspace/pro2-hil/bundles/87584f1/pro2-runtime-release`

Archive: `pro2-runtime-release-87584f1.tar.gz` beside it, SHA256 `2bf35f70e9fec70d4bab67935bf09661b31e69e14ce48c64f059f4f68a829ddf`

Manifest SHA256 `adc50c3d950f133adbf17c74137cf33cf37aab26971abb0b91e6ca3d71f8e6d2`, 367 runtime files.

| Artifact | SHA256 |
| --- | --- |
| pro2-sdk-bridge | e701973e0ae95ebccbfb47c547eff2761f8532f2ce8aad99a7ca42090170854b |
| xr-native-spike | 63ff348c14dc047f2f365094d7b61507a670f3d839a60254e50ea4763eb6c753 |
| libxr-ownership.so | 9587ca186ac1de176c8c30ee4b5862bf377318ce18af9cbb7fffec4f33bfb5bd |

Checked on a fresh extraction of that archive, under `/usr/bin/python3`: SHA256SUMS, 367 pins, preflight with no SDK and no compositor, and the genuine CLI matrix, 5 of 5.

| Suite | Result |
| --- | --- |
| Genuine CLI matrix, fresh extraction | 5 passed |
| tests/pro2_runtime | 45 tests, 5 skipped (those that need built binaries run in the smooth acceptance row) |
| tests/pro2_runtime_qa | 42 tests, 2 skipped |
| tests/pro2_smooth_qa (independent review) | 55 tests, 1 expected failure: too few distinct stops in the captures |
| smooth acceptance with built binaries | 16 passed |
| tests/xr_controller | 600 passed |
| tests/native_spike | 2559 run, 22 skipped; test_stabilization needs pytest, see below |
| xr_options_integration, stabilization, native test_stabilization (pytest venv) | 356 passed, 3 skipped |
| tests/pro2, pro2_diagnostic, xr_options_acceptance | 8, 29 (2 skipped), 6 |
| QML, offscreen | 49 passed |
| ctest, GPU on renderD129 only, private namespaces | 114 of 114 |

Nothing here was run in the glasses, on the SDK, or on a live compositor. The steps for that are in [../smooth-live-test.md](../smooth-live-test.md).
