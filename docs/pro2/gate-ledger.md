# Integrator gate ledger — parent evidence through labeled roll

| Gate | Evidence/status | Boundary |
| --- | --- | --- |
|G0identity/access|Parent confirmed35ca:1301 exactproduct; passive inventory captured|Node/connector addresses transient; permission is not compatibility|
|G1physical picture|Cody confirms right-side-up glasses and normal physical picture during G3; before/after1080p120readbacks|No optical measurement or causal claim about G2mode|
|G2stationary rawSDK|Parent authorized15s;902poses,60.1584Hz,clean exit; independently analyzed offline|Scoped to installed SDKhash/firmware; not productiontracking|
|G3four directions|Parent authorized30s;1805poses,60.1570Hz; actual cue-aligned settled samples recomputed|H1upstreambasis supported as offline candidate; roll/convention/opticaltilt not fullyvalidated|
|Offlineadapter|Replay-only H1candidate produces16float32packet shape; both private captures replay; synthetic clock/fault/teardown tests|No SDK/IPC/provider/production path connected; all activationguards stay|
|Roll/reference gate|Parent completed separately authorized20s;1203poses; H1signedroll consistent|Movementlabels confirmed; roll-run physicalpicture confirmation missing|
|Installation/XRlifecycle/reconnect/highHz/SBS|Notcleared|No installation, guard removal, XR start/stop or extra physical experiment authorized|

Evidence details and numerical/interpretation limits: [G3 report](hil-evidence/20261104-154541-directional/README.md). Existing private data remains private. Current integration changes are offline analysis and replay code only; no sibling worktree/owned transport/display code changed.

## Historical roll proposal — completed, do not repeat

One20s same pinned vendor Gen2rawpose session, noXR or install:0–5scenter/upright,5–10s left-ear-down small roll and hold,10–15s right-ear-down small roll and hold,15–20s originalcenter. Parent first obtains Codyconsent and reconfirms no competing provider/exactidentity/access, immediate mode readback and normal picture. Parent logs actual cue hosttimes, permits comfort to override timing and preserves private output. Same unshare-Urn and bounded timeout/close-stop-shutdown-destroy; no calibration/reset/opticalcorrection or device/display commands outside reviewed SDK lifecycle. No one should execute this based only on this ledger.

This isolates missing roll-axis behavior and helps distinguish convention assumptions. H1basis maps rawvector(x,y,z)→(-z,-x,y); standard active NWU left-ear-down would be negativeX/right-ear-down positiveX. Those are hypotheses to test against explicit physical labels, not mappings silently selected for production. Off-axis motion must be reported. Centerreturn is not an opticalreference. Capture timing/cleanup risk remains as in diagnostic-candidate.md; failure blocks advancement without automatic retry.

If clean and consistent, next work is native private replay/reader contract and reviewed provider lifecycle/time/recenter/FOV policy, then parent requests specifically scoped installation and supervised XRtest. A pure rollpass alone does not approve installation, opticalpitch correction, firmware update, SBS, disconnection tests or control of existingunits.

## Current next decision after roll

[Roll evidence](hil-evidence/20261104-155529-roll/README.md) and [native acceptance/integration plan](integration-plan.md) supersede the prior request for another motion session. Allthree signed axes support H1as an offline candidate; isolatednativewriter/reader/PoseState replays pass. No repeated posecapture is currently warranted. Parent reviews projection/FOV/opticalpolicy and the future specific bridgeartifact before any installation or XRgate. Roll physicalpicture confirmation is not yet supplied.15:47:27disconnect was confirmed manualunplug,not SDKinstability. Existingnormalguards remain.

## Historical first runtime build — superseded by RQ6/RQ7

Frozen92f01a7bundle includes realSDK→nativepublisherruntime, boundedwrapper, dedicatedcontrollerstart/recover and explicitprovisionalprojection. IndependentfinalQA117green plus287fileaudit; [runtimeevidence](runtime-evidence/README.md). NormalPro2guards stay. Rollphysicalpicture nowconfirmednormal. Prior recommendations tobuild/runtimeorcollectroll are historical and superseded. Nextapproval is concretebundle review/stagingifdesired and ONE separatelyauthorizedphysicalXRWorkspace test per runtime-runbook.md; no furtherofflinecodeblocker or repeatmotioncapture remains. Installation/physicalprojection/comfort/XRlifecycle are unproved.


## Current status: canonical packaged entry, frozen source 9158428

The parent authorized two physical XR attempts. Neither produced a physical XR picture: the first stopped at bundle plugin-path validation (RQ6); the second loaded the plugin but failed strict scope type identity (RQ7). Parent reports clean backout for both. No automatic retry is authorized.

RQ6 now validates the complete extraction layout and pinned plugin before constructing the client or SDK owner. RQ7 places TestScope, ScopedGuard and BundleClient in one canonical module, and the shipped shell imports canonical main. Strict scope validation and the normal production Pro2 guard remain unchanged.

The replacement runtime is frozen at `9158428f8daeb19fb8a8dbe28358c43252cb3c2b`. Its 330-file manifest SHA256 is `088343e0ceca1f10def4042baad3ac64db26f75aca3aabb0d1a9f763762192cb`. Fresh archive extraction, canonical imports, real plugin-command dispatch validation and the genuine packaged CLI four-case matrix passed offline. The matrix reaches real managed transactions, view controls, spawned guard and renderer launch through simulated external endpoints, including failure rollback and a second actual recovery CLI with no SDK calls. It does not execute real compositor Lua or prove a physical picture.

The 92f01a7 and 910cabc archives are historical and superseded for launch. Later review-only documentation may differ from the frozen runtime documentation; this ledger and the release review README carry the final handoff status. Parent must review the replacement archive and run packaged preflight at the final extraction location before deciding whether to authorize one fresh bounded physical attempt. Installation, actual compositor rendering, projection comfort and physical XR lifecycle remain unproved. No additional axis capture is currently required. Normal per-model settings and Luma/Beast support remain intact.
