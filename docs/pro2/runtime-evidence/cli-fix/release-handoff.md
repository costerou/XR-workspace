# Replacement release handoff

Frozen runtime source: `9158428f8daeb19fb8a8dbe28358c43252cb3c2b`.
Canonical class/entry fix: `189fc93`. Independent final artifact closure: `539b28a`, merged at `0985f9d`. Changes after the frozen source are review documentation/evidence only; the runtime bundle remains unchanged.

Archive: `/home/cody/.hermes/profiles/coder/previews/pro2-setup/pro2-runtime-release-cli-9158428.tar.gz`

Archive SHA256: `3b0d258976428c9a4b084b5409f822163844e97fcfa6dce2079e98b6a6e440f3`

Manifest SHA256: `088343e0ceca1f10def4042baad3ac64db26f75aca3aabb0d1a9f763762192cb`

The archive contains a complete `pro2-runtime-release/runtime` and separate `review` tree, release metadata, README and SHA256SUMS. All 509 release entries passed checksum verification after extracting the actual shipped tarball. All 330 runtime pins and canonical packaged source/entry checks passed. The combined auditor then ran the genuine CLI matrix from that extraction: 4 passed, zero skips, 11.588s; final hashes unchanged. Independent QA separately ran the same frozen runtime audit and matrix: 4 passed, zero skips, 12.165s.

Native binary hashes:

| Artifact | SHA256 |
| --- | --- |
| pro2-sdk-bridge | 359bf8c807b5aef1fcc1ea63bee930ad41f446d88dc7c0328cda2b67d3086a9d |
| xr-native-spike | c4af50d0667362f56b71168c8eb7f157b952c78f34452b9a5f4b968a42d07334 |
| libxr-ownership.so | 45ade12a6af7c80d695d33f8d334ca517b135174e661c7283e786a98b04db7f9 |

RQ7 was reproduced through the actual shell child entry, not only imported execute. Its __main__ class identity differed from managed's canonical import. The fix centralizes strict scope/client/guard types and uses canonical main. The real packaged CLI now traverses real managed setup, journal/output transaction, view controls/binds, spawned guard, renderer-launch boundary and native PoseState through simulated external endpoints. Matrix cases cover success, renderer failure, retained partial-create failure followed by a second actual recovery invocation with no additional SDK calls, and SDK-open failure before plugin load.

Broader regression logs: 42 independent QA, 10 pipeline, 534 controller, 8 default guards, 2474 native Python tests passed; one native test explicitly skipped; 10882 native subtests passed. No live render tests were used. Normal Luma/Beast/default Pro2 guards remain unchanged.

The final archive README gives checksum/preflight and complete-tree staging rules. No installation is required to run the verified bundle. At the FINAL location, first run `runtime/bin/pro2-supervised-test --preflight`; expect this exact source, 330 pins, validated plugin path, sdk_started false and compositor_contacted false. Any unsupported layout or changed pin must stop before client/SDK construction.

92f01a7 and 910cabc are superseded for launch. Neither parent attempt produced a physical XR picture. Another physical attempt requires the parent's separate decision and fresh consent/run ID/state, following runtime-runbook.md. No automatic retry is authorized. Projection remains an explicitly approximate 44.5-degree diagonal seed; optical correction is omitted as unmeasured. Real compositor Lua, physical rendering, comfort, install and hardware lifecycle remain unproved. No real SDK, USB/HID, GPU, live compositor, service or installation was touched by this work, and private parent logs were not accessed.
