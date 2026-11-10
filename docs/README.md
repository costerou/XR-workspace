# Documentation

## Using XR Workspace

- [User guide](user-guide.md): every feature and key, for day-to-day use.
- [VR video](vr-video.md) and [web video](web-video.md): formats, controls and limits.
- [Immersive 3D](immersive-3d.md): turning desktops into stereo 3D, setup and risks.
- [Head stabilization](stabilization.md): what the stabilization setting does.
- [Head-tracking drivers](own-drivers-default.md): which driver each pair of glasses uses, and the way back to
  XRLinuxDriver.

## Technical reference

- [Technical reference](technical.md): architecture, build and install, test status, safety design, CLI recovery,
  incident reports, running the tests.
- [Native code](../spike/README.md): the C++ renderer, Hyprland plugin and build options.
- [Driver performance](performance.md), [USB drops](usb-drops.md), [3DoF A/B](tracking-3dof-ab.md),
  [capture format](capture-format.md).
- 3D internals: [stereo renderer](stereo-render.md), [AI depth engine](depth-engine.md), [comfort](3d-comfort.md),
  [edges and strength](3d-edges-strength.md).

## Test plans and acceptance records

- [Placement pills glasses test script](placement-menu-test-plan.md).
- In-glasses options menu: [interface](xr-options-interface.md), [acceptance](xr-options-acceptance.md),
  [build report](xr-options-build-report.md), [live test plan](xr-options-live-test-plan.md).
- [`pro2/`](pro2/): Pro 2 bring-up, runtime contract, QA gates and evidence.
- [`evidence/`](evidence/), [`feasibility/`](feasibility/) and [`../analysis/`](../analysis/): raw test logs and
  investigation data cited by the documents above.
