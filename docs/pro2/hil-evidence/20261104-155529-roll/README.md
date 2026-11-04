# Parent-run roll capture: independent offline result

Parent reports ONE separately Cody-authorized20s SDK stream, exact identity35ca:1301/product and same installed library hash8dbe441e41aacf3592e94a94e8151a644c1a6f3749b2f684c33282b251b3e19e, ordinary user, unshare-Urn, bounded timeout and noXR/provider competition. Cody confirms center/left-ear-down/right-ear-down/center, facing forward. Parent result.json independently read exit0. **Cody has not explicitly confirmed the physical picture for this roll run.** Prior G3picture confirmation does not supply that missing observation. Parent reports no disconnect within this capture's kernel window, device remains present and USBnode unowned after exit; integrator performed no live inspection.

The15:47:27disconnect was Cody's confirmed manual unplug, NOT spontaneous and NOT SDK-attributed. Current device014/port1-5/rollingrecorder target1/1-5 are parent context, not a persistent identity or a new integrator hardware capture. No reset/replug/device command/SDK call performed here.

Private inputs read in place: roll.jsonl,cues.json,parent-result.json,display-before/after.json; existing quality.json structure is not substituted for recomputation. SDKprivate stdout/stderr were not read/copied; no raw poses/serials committed. Capture SHA2568fdfd3ac3362bd83a33e0427e34ca61b624ba5075ce433e5eaf4f1c9fdc6bf29; private directory suffix20261104-155529-roll. Committed JSON files contain only aggregates/provenance; relative rotation axes below are hold-level aggregates, not raw pose records.

## Quality, actual settled spans and display

1203samples/19.976536247s=60.1705913947Hz. Independently recomputed finite payloads/strict device+host timestamp ordering,zero issue samples,queue drops and cleanup errors. Firmware getter0:30.0.00.002_20260804. Paired clock OLS999,985,079.455SDKticks/hostsecond supports the prior bounded nanosecond-scale hypothesis; no clock reset/wrap/epoch/general latency contract follows.

Cue offsets relative firstpose:0.003843,5.003100,10.001182,15.000197s. Each settled window starts actualcue+2s and ends at min(cue+5s,nextcue,lastpose), so human reactions/transitions are not included by assuming perfectcue times. Timing still relies on Cody's compliance, not an instrumented motion trace.

| Hold | Samples | Actual sample span | Median-relative rotation magnitude |
| --- | --- | --- | --- |
|center|181|2.993101s|0°reference|
|left-ear-down|180|2.977083s|23.03358°|
|right-ear-down|180|2.972109s|23.20427°|
|return|180|**2.972067s**|4.23880°|

Final eligible settled bounds17.000197–19.976536s (2.976339s); nominal20s endpoint was not reached. Do not claim full3s final hold or exact20s receipt span. Return difference is manual return plus measurement/convention effects, not a proven drift/error.

Independently inspected DP-2before/after:1920x1080@120 both. This proves same readback for this run, not unchanged optical state/picture, and not SDK causation of any older mode change. No display API used by candidate.

## Roll composition and combined directional inference

Reference-relative H1axes:

* Left-ear-down(-0.999318,-0.001295,-0.036897), off expected rollXfraction0.036919.
* Right-ear-down(+0.965071,-0.080145,+0.249431), offXfraction0.261990.
* Opposed-axis dot=-0.973512. Significant right-side cross-axis content is retained, not labeled pure/precise roll.

Body-relative H1axes have offXfractions0.315155/0.515733; reference-relative composition is cleaner. These observations are consistent with neutral orientation affecting body/reference comparison; they do not measure optical tilt. Angle and inter-axis dot are invariant under the H1basis, not independent validations of that basis.

Together with prior G3left/right(+Z/-Z) and up/down(-Y/+Y), labeled left-ear-down(-X)/right-ear-down(+X) match a proper H1basis(w,x,y,z)→(w,-z,-x,y) under the standard active body-to-reference NWU convention. Raw identity would put yaw/pitch/roll families onY/X/Z rather than the targetZ/Y/X. Simple inversion of H1 at the same target convention reverses all six signed responses. Thus this is now **all-three-axis label-consistent candidate evidence**, stronger than four-direction-only support. It does not uniquely establish every coordinate naming/active-passive interpretation, absolute reference/gravity, renderer view inversion, FOV or optical mounting angle. No production mapping/guard was changed.

## Native acceptance completed OFFLINE

New native_contract.cpp is compiled against actual spike/src/pose.cpp and spike/driver/src/publish.cpp. Python runs it under `unshare -Ur -i`, a new user+IPC namespace. Publisher uses only a generated temporary prefix, private_segments=true with exclusive SysV allocation,history_step1,explicit mount_pitch0 as **no correction stage**,FOVNaN as unavailable,prediction config0. No existing/live IPC segment/socket/compositor is reached. FOVNaN is a test of unavailable metadata, not an optical value; calibration=true in the fixture is synthetic readiness for the publisher test, not SDK/hardware calibration proof.

All three private captures replay through real publisher→reader→PoseState without copying raw poses to disk/repo. Native checks current+two-history quaternion order/equality and fractional timestamps, finite views, initial reader disabled, absent FOV, software recenter to identity, stale100ms hold, pause disabled and destruction disabled. Existing PoseState and publisher suites also passed in a new IPCnamespace, exercising clock reset/float epoch, recovery, mutex/ownership and teardown regressions. Tests create temporary private IPC only; no SDK/device/GPU/compositor function is called.

The first native fixture failed because the fixture passed packetxyzw into publisher's internalwxyz type; the native equality assertion exposed it. The fixture now explicitly reorders xyzw→wxyz at the boundary, with matching raw/history assertions. No production type/ABI changed. RED log retained, final GREEN logs here. This catches a real integration hazard rather than weakening the check.

29diagnostic/adapter/analysis tests passed with rollprivate replay, including both nativefixture tests; G2andG3native replays2tests each passed separately. Existing native pose/publisher suites pass; app/provider guards remain tested. Full old rendering/GPU suites were not repeated because this change has no renderer/production/native source modification. Synthetic rollregression contains generated quaternions only, no private sample/median copied into fixtures.

## Next step, not another pose capture

No additional repeated yaw/pitch/roll SDK session is warranted by current evidence. See integration-plan.md for exact remaining code/artifact and external decisions. Parent may obtain missing roll-run physical-picture confirmation conversationally; do not invent it. Next narrow decision is review of the candidate projection/FOV and optical-correction policy, then preparation/review of a separately pinned runtime bridge artifact before any request to install/startXR. Existing normal activationguards remain closed. No install or newlive experiment is approved by this report.
