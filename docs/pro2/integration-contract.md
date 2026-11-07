# PRO 2 integration contract — 2026-11-04

Integrator-owned coordination contract. This records identity and safety boundaries, not hardware support.

* Canonical model key: `pro2`; exact product: `VITURE PRO 2 XR Glasses`; USB VID:PID `35ca:1301`. No alias to Pro, Luma Ultra (`1104`), or Beast (`1201`). Match VID/PID and exact product together; conflicting evidence fails closed.
* Model detection/settings belong to platform. Preserve independent saved profiles and every existing Luma/Beast default. No Luma correction or downward 8.5-degree adjustment on Pro 2.
* Provider selection belongs to driver. Pro 2 requires explicit exact-model evidence from an official provider revision. Enumeration is not tracking support. Unknown provider capability must reject before process launch, interface claim, proprietary command, or fallback to the Luma driver. Do not invent a quaternion mapping, packet parser, model enum, or SDK compatibility.
* Display belongs to display stream. Observed physical connector `card1-DP-2`, 1920x1080 modes, is inventory only. Connector names are not persistent identity. Do not infer 3D/SBS, refresh switching, or correction from old Pro or Luma. Preserve current physical mode; unsupported controls fail closed.
* Native publisher/prediction/calibration wire contracts remain unchanged. Any future official quaternion integration must document source coordinate system, timestamp units, handedness, normalization, reset and calibration semantics before activation.
* Hardware and protocol streams own evidence, fixtures and official citations only. QA owns offline acceptance and review. Integrator resolves shared-file conflicts and minimal existing-pattern labels only; no UI migration/redesign.
* Build and tests are offline only: ownership plugin ON, isolated fake Wayland and explicit `/dev/dri/renderD129` where rendering is needed. No live compositor hooks, discrete GPU tests, installs, service changes, or device commands. Native tests must not add compositor hooks.
* Parent alone seeks Cody consent for installation, desk checks and axis moves. No installation or live tests authorized here. B1/C1UI, xr-options-build and drop-incident-evidence remain separate.

Reports: `/home/cody/.hermes/profiles/coder/previews/pro2-setup/`. Each sibling should publish early, commit its owned files and notify integrator when safe to merge, with exact head and test evidence. This contract may be tightened by evidence; compatibility must never be broadened by assumption.
