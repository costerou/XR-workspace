# Built Pro2 supervised-test runtime — parent review and later authorization

Normal Pro2 panel/driver/managed activation stays blocked. This bundle adds a **separate bounded test route**, not an unattended production driver. Cody authorized its offline build; installation, SDK/hardware execution and physical XR Workspace testing are NOT performed/authorized by this thread. Roll picture is now confirmed normal by Cody (supersedes the earlier missing confirmation). Private raw/SDK logs stay private.

## What is built

`bin/pro2-sdk-bridge`: actual native vendor SDK owner→bounded callback queue→existing native Publisher, with H1mapping, ns-scale relative clock, history1, explicit provisional diagonalFOV, no optical correction/prediction constant. Dedicated statusFD is separate from vendor output. `bin/xr-native-spike`: explicit testprefix/projection preflight + approximate statuslabel, preserving old defaults outside testflags. `bin/libxr-ownership.so`: same ownership source, no input/cursor/surface hooks. `bin/pro2-supervised-test`: direct controller route using pinned bundle artifacts, existing managed windows/journals/Stop/recover, no installed.json/controllerunit change. Runtime Python/source/docs/QML are included and hashed. Synthetic binaries/SDKs and private captures are NOT packaged.

Installed prerequisites: ordinary-user SDK `/usr/lib/xr-driver/libglasses.so` SHA2568dbe441e41aacf3592e94a94e8151a644c1a6f3749b2f684c33282b251b3e19e, firmware30.0.00.002_20260804, exact35ca:1301 + `VITURE PRO 2 XR Glasses`; Python3.11+ (hashlib.file_digest), unshare user/network namespace support, OpenSSLlibcrypto, libc/libstdc++/libgcc/libudev and the renderer's already installed shared libraries. Native worker is dynamically linked without SDK at buildtime. Manifest records ownership Hyprland/Aquamarine ABI; parent checks it still matches before pluginload. No package/dependency/udev/service installation was executed. Existing vendorwideudev access already covered this device; do not add/reload/trigger rules for this test.

## Offline build/review commands (no SDK loading)

From this branch, build ownershipON, then:

```
cmake -S spike -B build-pro2 -G Ninja -DXR_ENABLE_OWNERSHIP_PLUGIN=ON
cmake --build build-pro2 -j 4
python -m tools.pro2_runtime.package --build build-pro2 --destination build/pro2-runtime-ready
python -c 'from tools.pro2_runtime.launch import verify_manifest; verify_manifest("build/pro2-runtime-ready"); print("hashes verified")'
python tests/pro2_runtime/run.py
python tests/pro2_runtime_qa/run.py
```

Package destination must be new. Worker productioncompile has no fixturearguments/hashoverride. Tests compile a different PRO2_SYNTHETICbinary and syntheticC SDK; testhelpers establish fresh user+IPC+network namespaces. Tests never load installed SDK. CPU-only CTest excludes `(render|^overlay_residue$|^gl_frame$|^capture_e2e_)`; no live/render/GPU tests were run. See runtime-evidence and runtime-qa.md for exact successes, RQ1–RQ5 red/green and limits. The renderer/worker package was rebuilt after recoveryfixes; old be41233reviewbundle is obsolete.

## Optional later isolated installation (parent approval required)

The test can run from the verified bundle in place; no live install is required. If parent authorizes staging under user data, copy the **complete immutable bundle** to a new versioned directory, e.g. `$HOME/.local/share/viture-workspace/pro2-supervised/<manifest-source-commit>/`, preserving files/permissions. This is testartifact staging only, not changing current installed.json, controller source, systemd units, normalprofiles, plugin enablement or origin/master. Reverify manifest at destination. Do not replace `/usr/bin/xrDriver`, SDKlibraries, normalrenderer or QML. Current normal installation remains available for backout.

Exact SDK gate cannot be bypassed by manifest: worker checks fixed installedSHA and exactfirmware, Gen2/PID60posecapabilities, identity beforedlopen/create and duringloop. Wrapper refuses known same-user providers/maps. Parent must establish exclusive device/topology scope: no competing SDK/xrDriver/Lumaprocess, no otherVITURE device, no cable changes while test runs. SDK selects byPID, not a devicefd; this is **not atomic hotplug confinement**. A change/removal/additionalVITURE causes terminaldisable+cleanup, not reconnect/reset. Preserve the no-other-provider condition for the bounded test. Do not stop another provider/unit automatically; a conflicting process blocks this test.

## Exact first supervised XR test preparation — do not run without parent consent

Parent obtains explicit approval for one bounded SDK+XR Workspace test: approximate optics, explicitoutput/recoveryoutput/windowcapture, ownershipplugin, normal temporary XRbinds and window restoration, no displaymode/refresh/SBS/firmware/calibration commands. No service/controller restart. Keep ordinary physical fallback reachable. Parent passively rechecks current exactUSB/product/access and monitors/instance. Do not reuse transient bus/device IDs as identity. Choose actual compositor instance and an independent physical recovery connector from fresh parent readback; never guess/reuse stale values.

Create a600private consentJSON outside the bundle (substitute verified instance/fallback; DP-2 is the reported glassesoutput, reverify it). Example scope:

```
{
 "schema":1,"gate":"pro2-supervised-v1","model":"pro2",
 "run_id":"pro2-supervised-001","instance":"VERIFIED_INSTANCE",
 "output":"DP-2","recovery_output":"VERIFIED_PHYSICAL_FALLBACK",
 "seconds":120,"projection":44.5,
 "parent_authorized":true,"exclusive_topology":true,"approximate_optics":true,
 "desktops":[{"width":1920,"height":1080,"scale":1}]
}
```

Use fresh runid for each test. Template placeholders are not authorization and must be replaced before use.120seconds is the maximum SDKlease for this first proposed scope, including time to inspect/stop; normalStop should occur earlier, not wait for expiry. Value44.5 is **explicit provisional diagonal test seed from upstreamTBD**, not measured/safest/calibratedFOV. Optical correction is omitted as unmeasured, not physicaltilt0. Consent validation rejects missing/nonfinite/out-of44.5..50projection or implicit/normalmodelscope. NormalXRguard staysclosed. Renderer preflight requires matching publishedprojection+privateposefeed; no silent46fallback or Luma52. Runtime renderer status names `pro2-supervised-approximate`.

Parent-authorized launch only:

```
BUNDLE=/absolute/verified/pro2-runtime-ready
STATE=/absolute/new-private-test-state
mkdir -m 700 "$STATE"
"$BUNDLE/bin/pro2-supervised-test" --consent /absolute/pro2-test-consent.json --state-dir "$STATE"
```

Controller launches worker inside ordinary-user networknamespace; SDKoutput goes only to `$STATE/<run_id>/sdk.private.log`600. Never share/commit it; it can contain serials. Status/health/process files contain bounded operational metadata. Appwindowcapture is only of this run's managedoutputs; tests use per-test stateprofiles, no normalper-model settings migration. Pro2route never calls LumaSDK/fallback, mode_switch or ButtonSbs restore. Current physicalmode preserved by testpolicy; parent records before/afterreadback and physicalpicture, never infers historicalcausation from earlier60Hz.

At firstimage, verify unmirrored upright tracking/center/roll response, comfortable projection, ordinarydesktopreachability; recenter via existing workspace control (software only). No forced longsoak or repeated SDKaxis session. Stop immediately on discomfort, inversion, wrongprojectionlabel, disconnect or unexpectedmode. Do not experiment with buttons/SBS/highHz/reset. A successful callback/nativefixture is not proof of renderedcomfort/latency or opticalcalibration.

## Stop, failure and recover

Use existing normalStop control in the test workspace or Ctrl+C in the foreground testCLI. CLI SIGTERM is converted to an interrupt so managedfinally restores windows/ownership before wrapper terminates its exact childprocessgroup. Do not signal normalcontroller or viture-xr units. No autoreconnect/fallback starts. SDKlease expiry/fault disablesfeed and fails healthguard, ending renderer; a forcedexpiry is not a clean successfulXRtest, even if ownershiprecovery completes. Overallsuccess requires rendererreturncode0 and completecleanup.

Runtime starts no feed until3valid samples; startup no-pose3s, streamstale100ms, invalidnorm/nonfinite/clockbackward/duplicate/wrap, queueoverflow/null and identitychange are terminal. Feed pauses before SDKclose→stop→shutdown→destroy. Wrapper imposes startup10s/walllease+10s and five-secondTERMthenKILL ceiling for opaqueSDKhang. Hardkill/parentcrash cannot prove native cleanup; preserve logs and passiveidentity/access evidence, no automaticUSBcommands/replug. ProcessgroupPID is recorded in private process.json; never kill by broad processname.

If result reports incomplete recovery, ownershipplugin is deliberately retained. With SAME immutablebundle, SAMEconsent and SAMEstate, parent-authorized explicitrecovery:

```
"$BUNDLE/bin/pro2-supervised-test" --consent /absolute/pro2-test-consent.json --state-dir "$STATE" --recover
```

Recovery starts NO SDK/provider and performs no legacySBS restore. It validates canonicaljournal/session/ownership, restores windows/outputs, and unloads pinnedbundleplugin only after confirmed complete recovery. If incomplete, retain plugin/journal and report reasons; do not run a new session or forcibly unload. A changed compositor epoch requires existing explicit CLIrecovery review, not guessed mutation. Keep logsprivate; sanitized result includes runid/status/reasons but may need privacyreview before externalsharing.

Backout after confirmedcleanrecovery: verify no testworker/renderer remains and ordinarywindows/display usable; keep normalinstalledsetup untouched. Archive private evidence locally. Optionally remove the separately staged testbundle/state only after reviewing retainedjournal needs; no uninstall/service/master changes required. Do not remove normalconfig/profiles or guessedUSBnodes. A clean test does not authorize permanentenablement; parent reviews source/hash/results before a separate activationchange.

## Bounded projection calibration if first test permits it

No autosweep. After stopping/restoring, parent may separately approve one new scopevalue within44.5..50 in at most1°step, each newrunid, record comfort/edgegeometry/centerrelative tophysicalpicture. Never relabel marketed50 as confirmeddiagonal or infer opticalpitch from neutralquaternion. Stop at discomfort; do not require reaching50. A provisionalcomfortablevalue remains provisional, not instrumentedoptics. Normal route continuesrefusing unresolvedoptics. Independentprojection-policy.md explains provenance and assumptions.

## RQ6 first-launch correction — supersedes the original archive

The first parent-authorized attempt ended before plugin load/rendering with `supervised bundle plugin pin required`. Parent reports the runtime SDK owner closed cleanly, no XR outputs/plugin remained, USBnode unowned and1080p120unchanged. No physicalXRpicture was produced. This was **not a passed XRtest**, and no automatic retry was authorized. Private SDKlogs were not read/copied by integrator.

Root cause: supervised BundleClient incorrectly reused normalinstalledPLUGIN_PATH, whose topology requires `/viture-workspace/bin/<12hex>/libxr-ownership.so`. Hyphens themselves were allowed. A fully hashed extraction such as `/home/cody/.hermes/profiles/coderbotxr/cache/scratch/pro2-release-review-z3nsa4vu/pro2-runtime-release/runtime/bin/libxr-ownership.so` did not meet that unrelated topology. Earlier tests injected a fakeclient that skipped real plugincommandvalidation; new tests use realBundleClient with only the compositorrequest boundary mocked.

Corrected contract: supervised bundles may be run directly from a verified extraction. Root and ancestor directories must be canonical, non-symlink, absolute after normalization, with ASCII components `[A-Za-z0-9_.-]+`, no whitespace/control/protocoltokens or `.`/`..`aliases. Fixed suffix is exactly `bin/libxr-ownership.so`; every artifact remains manifest/hash-pinned; symlinkroot/bin/plugin and changedbytes reject. The normalinstalledregex/pins remain untouched. This is not permission to use arbitrary pluginpaths.

**Mandatory before requesting any retry**, run the new packaged preflight at its final extracted/staged location:

```
BUNDLE=/absolute/verified/new-release/runtime
"$BUNDLE/bin/pro2-supervised-test" --preflight
```

Preflight needs no consent/state and creates neither; it verifies the same exact pluginpath/hash contract used by load/unload plus all manifestpins, without SDK/client/compositor initialization. Expect `plugin_command_path:"validated"`, `sdk_started:false`, `compositor_contacted:false`, and the expected correctedsourcecommit. Unsupported locations fail before any compositorclient or SDKowner is constructed; packager also refuses unsupported destinations before compilation. Do not patch the old artifact in place or reuse its stale manifest.

No special normal-install-layout staging is required. If parent elects versioned staging after review, an exact supported pattern is `$HOME/.local/share/viture-workspace/pro2-supervised/<FULL_CORRECTED_MANIFEST_SOURCE_SHA>/runtime/` holding the entire runtime tree. Use a new directory, preserve all files/modes, verify hashes and preflight THERE. This does not touch normalinstalled.json/services. The old `pro2-runtime-release-92f01a7.tar.gz` is superseded for launch; retain solely for incidentprovenance. Parent separately decides whether to authorize a new run with a fresh runid/state/consent. The previous consent does not authorize automatic retry.
