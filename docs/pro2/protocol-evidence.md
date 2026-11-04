# Pro 2 protocol/provider evidence — 2026-11-04 (preliminary)

Scope: exact `35ca:1301`, product `VITURE PRO 2 XR Glasses`, key `pro2`. Research only; no device opened or SDK loaded. Integration contract read and accepted.

**Immediate lead handoff:** official SDK documents Pro 2 as Gen2, distinct from Gen1 Pro and Carina Luma Ultra. Existing local xrDriver source explicitly names Pro 2, but applies a Pro2-specific coordinate conversion and -8.5° pitch adjustment, with FOV marked TBD. Official current docs instead describe Gen2 output as NWU. Therefore existing provider support is real source evidence, but unmodified launch is NOT cleared under the shared no-unproven-correction contract. Driver lead must keep activation gated pending reconciliation. Local source also explicitly disables Pro2 SBS.

Primary sources: https://www.viture.com/developer/glasses-sdk/glasses ; https://www.viture.com/release-updates/sdk ; https://github.com/wheaney/XRLinuxDriver/releases/tag/v2.11.9 . Detailed source manifest and contract follow in this report.

## Evidence ranks and decisions for leads

A = current official vendor documentation; B = pinned upstream implementation and local binary static evidence; C = independent reverse engineering; U = unresolved. These are evidence ranks, not device test results.

**Driver:** prefer the vendor `libglasses.so` Gen2 provider, never the Luma transport. Exact PID support exists in the installed binary (static disassembly below), and Pro2-specific handling exists in released upstream. Activation remains blocked by the correction/axis conflict and unverified firmware. Preserve the publisher, prediction and recenter contract; do not duplicate conversion downstream. Missing capability, firmware evidence or model mismatch must reject before launching anything. Do not treat a version string alone as capability proof.

**Display:** retain observed 1920x1080 physical output. Do not enable SBS from global SDK constants. Local released upstream expressly sets `sbs_mode_supported=false` for Pro2. An SDK get/set success and post-change EDID/mode observation would need a separately authorized live gate; none occurred here.

**Platform:** exact identity match is `pro2` / `35ca:1301` / `VITURE PRO 2 XR Glasses`; SDK market name is separately `VITURE_MARKET_NAME_PRO2` (`"Pro 2"`). Keep these namespaces separate. Do not infer native DOF, camera, tint or control ranges from Gen2 family membership. No permission-rule edits in this stream.

## A: official current documentation

- [SDK guide and public C reference](https://www.viture.com/developer/glasses-sdk/glasses): Pro2 is Gen2, 3DoF, no camera. Use `xr_device_provider_create`, lifecycle functions, `xr_device_provider_register_imu_pose_callback`, `xr_device_provider_open_imu`. Documented orientation is NWU; callback layout is Euler then scalar-first quaternion; timestamp is device timebase.
- Same reference: `xr_device_provider_is_product_support_imu_frequency` documents Pro2 pose at 60/90/120 Hz; raw supports 60/90/120/240/500/1000 Hz. Capability-check exact mode and PID. Brightness/volume 0..8; no film; native-DOF capability requires its own query.
- [Release notes](https://www.viture.com/release-updates/sdk): Gen2 pose/wear need updated firmware and pose opt-in. Notes mention a stop/release crash fix affecting Pro2. Direct retrieval returned an empty release body; indexed official text supplied these findings. Exact minimum firmware/build and release heading remain unverified.
- [Calibration tool](https://www.viture.com/firmware/calibration): Pro2 requires no IMU calibration.
- [Official Pro2 academy](https://www.viture.com/academy/product/viture-pro-2-xr-glasses) and [specifications](https://www.viture.com/ja/academy/xr-glasses/pro-2): marketed FOV 50°, per-eye 1080p, up to 120 Hz. This does not establish renderer horizontal/vertical FOV or Linux SBS support.

## B: upstream source and local 2.11.9 inventory

Local source root used throughout:
`/home/cody/.local/share/viture-workspace/xrdriver-3dof/2.11.9-958908-pr147-pr148-3dof/src`.
These file/line references describe inspected files, not code executed in this task.

Current upstream main queried through GitHub API is `958908487a36aad15c6cbb5b7865e404c7b7dfac`. Latest release API says `v2.11.9`, published `2026-09-14T23:27:34Z`. Release source is [36e2139dd482f5984112d0c3ebafcc07a78f99c0](https://github.com/wheaney/XRLinuxDriver/tree/36e2139dd482f5984112d0c3ebafcc07a78f99c0). [Release history](https://github.com/wheaney/XRLinuxDriver/releases) identifies v2.11.4 as Pro2/axis improvements and v2.11.9 as SBS regression repair. A general SBS repair does not override the model-specific disable.

Pinned [src/devices/viture.c](https://github.com/wheaney/XRLinuxDriver/blob/36e2139dd482f5984112d0c3ebafcc07a78f99c0/src/devices/viture.c):

- Lines 50–56 explicitly separate Pro and Pro2 model entries.
- Lines 61–112 contain Pro2 pitch -8.5°, FOV 44.5 **TBD**, 1080p height, calibration wait 1 second, look-ahead constant 20. These are upstream defaults, not validated physical calibration. The pitch default violates this bring-up contract until justified/removed in a separate reviewed change. Do not copy it.
- Lines 144–160 choose highest SDK-advertised frequency, but fall back to 240 Hz if none are advertised. **Do not copy that fallback**: it exceeds documented Pro2 pose limits; fail closed when no supported rate exists.
- Lines 449–469: `viture_pose_callback` reads quaternion indices 3..6; divides timestamp by 1,000,000 into uint32 milliseconds; `viture_publish_pose` applies `quaternion_eus_to_nwu` when `requires_coordinate_adjustment` is set.
- Lines 707–735: accepted products pass `xr_device_provider_is_product_id_valid` and SDK market-name lookup; Pro2 gets `requires_coordinate_adjustment=true`; Pro2 and Beast have SBS disabled. This conflicts with generic NWU documentation: do not infer which is correct for today's firmware.
- Lines 788–800 register pose callback for Gen2 without native DOF; native-DOF devices take raw/fusion. Lines 833–855 open IMU using selected mode/rate. Provider initialization and start/open calls are active operations, forbidden in this task.
- `src/imu.c:60–62`: EUS→NWU mapping preserves w and maps x=-z, y=-x, z=y. Record this as upstream behavior, **not** an approved Pro2 transform. Official NWU output would require no such conversion before an NWU publisher.

Local SDK headers:

- `include/sdks/viture_version.h:10–12` declares 2.4.0; `viture_protocol_public.h:50` defines Pro2 market name.
- `include/sdks/viture_device.h:35–45` documents NWU callback, scalar-first layout, device-timebase timestamp. It does not document nanosecond units. Nanoseconds are an upstream interpretation, still a vendor clarification/live timestamp gate.
- `include/sdks/viture_glasses_provider.h:169–195` declares exact-PID validity, native DOF and frequency capability queries and market-name retrieval.
- `include/sdks/viture_protocol_public.h:62–66` defines standard 1080p modes: 1920x1080 60/90/120, 3840x1080 60/90. Constants alone do not promise per-model support. Native-mode enum values differ; do not interchange them.
- `include/sdks/viture_protocol_public.h:156–161` maps rate indices 0..5 to 60..1000 Hz. A USB endpoint interval is not a delivered pose-rate guarantee.

The A/B build manifest reports source `248fc87690d55c58ccc16d4e6bba19cd957373af`, based on main `9589084` plus PR147 `7827aab10a7e450d06f1a1f764ba9bc4cbc3e134` and PR148 `29c3d108d6cc111be8e6508403db1d0fdc58e219`, then Carina-only 3DoF patch. This is distinct from installed release 36e2139; preserve reconnect and access fixes. The patch is not Pro2 enablement.

Static library inspection (no `dlopen`, ctypes, `ldd`, SDK call or executable launch):

- A/B `src/lib/x86_64/viture/libglasses.so` SHA256 `a68cbef5e39fa97086fe6e8d63a2a3649165c78c298f85ba896ff12ef75404ad`, matches manifest.
- Installed `/usr/lib/xr-driver/libglasses.so` SHA256 `8dbe441e41aacf3592e94a94e8151a644c1a6f3749b2f684c33282b251b3e19e`; different file hash means do not assert byte-identical whole libraries. Installed strings include `Pro 2` and `2.4.0`; this is not an executed version query.
- `nm -D` confirms A/B exports `GetVersionString`, `xr_device_provider_create`, `xr_device_provider_get_market_name`, `xr_device_provider_open_imu`, `xr_device_provider_register_imu_pose_callback`.
- Installed `objdump -d --disassemble=xr_device_provider_is_product_id_valid`: address `0x65f55` compares `%edi` to `0x1301`, then `sete %al` and returns. This is exact PID acceptance evidence in binary; it does not establish tracking readiness or correct axes.
- Installed `readelf -d` dependencies include libudev, libstdc++, libm, libgcc_s, libc and ELF loader. Nothing was loaded.
- Cached AUR `PKGBUILD` pins release 36e2139 and installs VITURE libraries under `/usr/lib/xr-driver`; inspected as text only. Its prepare/build/package functions were not invoked.

## C: public wire protocol research is available, but unapproved

[elasticjava/viture-v2](https://github.com/elasticjava/viture-v2), specifically [PROTOCOL.md](https://github.com/elasticjava/viture-v2/blob/master/PROTOCOL.md), claims testing exact 35ca:1301 on firmware `30.0.00.002_20260804`. It is independent reverse engineering, **not official VITURE protocol documentation** and not our capture. It describes HID class interface 0, interrupt OUT 0x01 / IN 0x81, 64-byte transfers and 1ms interval. Those transport claims require hardware-stream passive descriptor comparison. No packet layout or command is adopted here. Do not copy initialization, start commands, fusion or parser from it into bring-up based on these claims. Vendor SDK remains preferred.

## Known failures and unresolved gates

1. Generic official axes vs explicit Pro2 upstream transform are inconsistent. Vendor confirmation scoped to SDK build + firmware, followed by authorized controlled axis tests, is required. Never apply both transformations.
2. Timestamp units, epoch/wrap behavior, quaternion normalization and yaw/reset behavior are not adequately established for exact Pro2 firmware. Preserve current publisher ABI; reject invalid/stale/nonfinite samples in offline tests. No Carina prediction/reset API on Pro2.
3. Exact minimum firmware is unknown. Presence of symbols or PID acceptance cannot establish pose availability. No firmware read/update occurred.
4. Upstream v2.11.9 SBS regression fix and vendor stop/release crash notes are relevant software risks; no evidence here establishes a Pro2-specific USB disconnect root cause. Existing Luma command failures, crashes and reconnect incidents are not Pro2 findings. Preserve cooldown/stabilization fixes and test teardown offline.
5. Physical marketed FOV is not a measured projection matrix; upstream 44.5 TBD cannot be silently treated as calibrated. No lens/downward pitch correction approved.
6. SDK mode constants, marketing combined resolution and third-party SBS observations cannot override explicit upstream Pro2 SBS disable. Keep controls gated.

## Next safe gates (not performed)

Offline driver review first: exact identity/provider allowlist and hash/version provenance, unsupported rejection before launch, no Luma fallback, no -8.5° Pro2 correction, rate selection limited by supported pose capabilities, existing models unchanged, teardown/reconnect replay and unchanged publisher/calibration semantics. Resolve axis/timestamp/firmware gaps with vendor or build-specific authoritative evidence. Parent then requests authorization for a minimal SDK session with firmware capture and controlled axis/rate/teardown checks. Display/SBS is a separate later gate. Never promise working hardware from static acceptance alone.

Validation for this documentation-only stream: local manifest parsed; SHA256 matched A/B library; static symbols and installed exact PID branch inspected; pinned release source cross-checked; latest release/main API metadata fetched. No runtime/hardware tests. No functional code changed.

## Provider-update disposition — follow-up

**Recommended enablement artifact/revision: NONE VERIFIED.** Do not encode a minimum version or recommend installing a guessed SDK update. The checked current upstream main `958908487a36aad15c6cbb5b7865e404c7b7dfac` and latest published v2.11.9 do not supply evidence resolving the Pro2 correction conflict. The vendor release page did not yield a verifiable downloadable artifact/checksum tied to that fix. Local 2.4.0 headers/strings are not proof that a newer package is needed or sufficient. The SDK/provider *path* recommendation remains valid; a specific replacement artifact remains unresolved.

Driver stream supplied the following installed inventory (reported by that stream, not independently remeasured here): package `xr-driver-git 2.11.9-1`; cached source HEAD `958908487a36aad15c6cbb5b7865e404c7b7dfac`; `/usr/bin/xrDriver` SHA256 `184bcbaf4b6beab0df785489abb115dead923234b39703fdb2e90de7be6c8fb0`; installed `libglasses.so` SHA256 `8dbe441e41aacf3592e94a94e8151a644c1a6f3749b2f684c33282b251b3e19e`; installed `libcarina_vio.so` SHA256 `9a23e3ede795de7f6d6137a71755c57d3c9aba6055e96298ca8e8b266d3d4784`. Cached source HEAD must not be described as proof of the installed executable's source revision.

Recommended prelaunch policy: block if PID is 0x1301 **or** exact product is Pro2, including contradictory identity evidence, before start/reuse/fallback. Lift only for a reviewed artifact with recorded binary/library hashes, explicit exact-PID support, correction/coordinate evidence tied to SDK and firmware, timestamp contract, supported pose frequency, and lifecycle/reconnect offline validation. A hand-edited zero pitch or deleted quaternion transform alone is not compatibility proof.

Next evidence acquisition: obtain an official Linux x86_64 SDK bundle or upstream provider revision accompanied by Pro2-specific coordinate/pitch clarification, release provenance and minimum firmware. Record archive URL/version/SHA256 and headers/library hashes before considering installation. No vendor contact, download/install or hardware session was performed in this follow-up.

## Provenance clarification — installed versus cached versus 3DoF

Driver stream confirms cached upstream HEAD blob hashes (from `git show HEAD:lib/x86_64/viture/...`, not installed files):

| Evidence set | Source identity | libglasses.so SHA256 | libcarina_vio.so SHA256 |
| --- | --- | --- | --- |
| Installed | Package 2.11.9-1; executable source revision not established by cache | `8dbe441e41aacf3592e94a94e8151a644c1a6f3749b2f684c33282b251b3e19e` | `9a23e3ede795de7f6d6137a71755c57d3c9aba6055e96298ca8e8b266d3d4784` |
| Cached upstream Git blobs | HEAD `958908487a36aad15c6cbb5b7865e404c7b7dfac` | `a68cbef5e39fa97086fe6e8d63a2a3649165c78c298f85ba896ff12ef75404ad` | `ed571faa1435abc622fd54770152b936dcaafb7e7fb603fda2e994bf94f19bdf` |
| Local 3DoF manifest | `248fc87690d55c58ccc16d4e6bba19cd957373af`, includes PR147/148 and Carina patch | `a68cbef5e39fa97086fe6e8d63a2a3649165c78c298f85ba896ff12ef75404ad` | `ed571faa1435abc622fd54770152b936dcaafb7e7fb603fda2e994bf94f19bdf` |

The two installed library file hashes differ from cached upstream. This establishes different file identity, not necessarily different executable code, ABI or behavior; those require separate evidence. Conversely, cached/3DoF matching library hashes do not make their executables equivalent: the 3DoF source adds PR147/148 and a Carina-specific patch. No source-to-installed-binary provenance or compatibility claim follows from cached HEAD. The installed PID acceptance disassembly remains evidence specifically about the installed `libglasses.so`; upstream source behavior remains evidence about the pinned source. There is still no approved replacement artifact or compatibility enablement.
