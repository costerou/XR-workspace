# Pro 2 provisional supervised-test projection

Recommendation for the integrator, based on `1d141f8`: use **44.5 degrees diagonal**, explicitly labeled **provisional / upstream TBD**, for the first short supervised 2D visual test. This is a practical starting hypothesis, not an established Pro 2 calibration or a proven safest FOV. Keep the normal production Pro 2 guard. Require the separate explicit supervised-test route, exact `35ca:1301` plus `VITURE PRO 2 XR Glasses`, and later parent authorization for activation. This document authorizes no live action and changes no runtime code.

Do not apply an optical pitch correction. Omitting a correction stage is not a claim that physical optical pitch is zero. Preserve existing recenter semantics. The parent now confirms the roll picture was normal and movement prompts were followed; the directional evidence and native replay support an H1 candidate, not optical FOV, lens alignment, or mounting angle. Scope is the reported SDK SHA256 `8dbe441e41aacf3592e94a94e8151a644c1a6f3749b2f684c33282b251b3e19e` and firmware `30.0.00.002_20260804`; no new hardware test was performed here.

## Evidence and limits

1. [Exact Pro 2 vendor academy](https://www.viture.com/academy/xr-glasses/pro-2), tech specs: 1920×1080 per eye, marketed FOV 50°, 146-inch virtual screen at four meters, PPD 43. The retrieved page does **not identify the FOV axis** or provide an intrinsic matrix. Its fitting guidance covers diopters, nose pads and temple positions; its eye-axis control is distinct from software pose calibration. Those controls supply no measured IMU-to-optics rotation. Do not operate them in this test. This source supports product specifications, not a calibrated renderer projection.
2. Pinned [upstream viture.c](https://github.com/wheaney/XRLinuxDriver/blob/958908487a36aad15c6cbb5b7865e404c7b7dfac/src/devices/viture.c#L46): model list lines 46–55 distinguishes old Pro from Pro 2; lines 68–83 assign Pro 2 FOV **44.5 // Pro 2 (TBD)** and 1080p height. Its separate pitch array lines 57–66 assigns -8.5 to Pro 2. We adopt neither that pitch nor any Luma optical correction. No measurement provenance accompanies the TBD FOV.
3. Pinned [upstream outputs.c](https://github.com/wheaney/XRLinuxDriver/blob/958908487a36aad15c6cbb5b7865e404c7b7dfac/src/outputs.c#L133), lines 136–140, explicitly calls the device FOV diagonal. That dead-zone calculation approximates angular scaling linearly; do not copy that approximation into the renderer. It establishes upstream semantic intent, not physical correctness.
4. Local source at `1d141f8`: `spike/src/pose.cpp:103–108` reads the published FOV float and rejects unavailable/nonfinite/out-of-range metadata. `spike/src/wayland.cpp:495–503` interprets it as diagonal and computes perspective half-angle tangents from actual output width/height. Missing metadata uses **46°**, an inherited generic fallback, not a Pro 2 measurement. Report fields at lines 1084–1085 distinguish `driver` and `fallback-46`, but `driver` alone does not mean calibrated; the supervised route must also record provisional provenance. `spike/src/video.cpp:69–72` uses the same diagonal formula; lines 74–81 derive eye viewports. `spike/src/video_mode.cpp:88–90` projects video per eye. Do not change the working Luma/Beast values.

No exact Pro 2 primary source retrieved here establishes diagonal versus horizontal 50°, optical center, distortion coefficients, or optical pitch. Absence in these inspected sources is not a claim that such data cannot exist. Search terms included exact Pro 2 + diagonal, FOV50 and eye axis. Marketing geometry below is a consistency check, not a substitute measurement.

## Executable geometry and interpretation

At 16:9, for diagonal D and aspect a, set t=tan(D/2), half_w=t*a/sqrt(1+a²), half_h=t/sqrt(1+a²). Horizontal H=2atan(half_w), vertical V=2atan(half_h). These are rectilinear perspective calculations, not linear ratios of degrees.

Calculated values (degrees, rounded to six decimals):

| Interpretation | Renderer diagonal | Horizontal | Vertical |
| --- | ---: | ---: | ---: |
| Upstream Pro 2 TBD seed | 44.500000 | 39.249517 | 22.682792 |
| Inherited renderer fallback | 46.000000 | 40.605104 | 23.511341 |
| If marketed 50 means diagonal | 50.000000 | 44.235848 | 25.754484 |
| If marketed 50 means horizontal | 56.295070 | 50.000000 | 29.394958 |

Literal 146-inch diagonal at 4 m gives D=49.740162°. This is consistent with a rounded diagonal50 interpretation but depends on treating a marketed screen-size/distance pair as literal. Likewise 1920/43=44.651163° only under a uniform horizontal-average PPD assumption. Neither is a measurement or definitive axis proof. Do not use 50 horizontal or 56.295 diagonal as the initial setting.

A smaller projection angle is not automatically more comfortable: with a larger true physical FOV it can magnify angular head-motion response. At fixed scene geometry increasing the renderer FOV shrinks projected objects; decreasing it enlarges them. Thus the modest upstream seed is chosen for provenance and bounded experimentation, not a claimed safety ranking. Motion response, latency, prediction and screen placement can confound an FOV judgment.

## Proposed supervised visual gate (not executed)

1. Parent authorizes a short seated test only after the integrator's test-only route and QA acceptance pass. Preserve the current 1920×1080 physical mode and refresh. No display mode, refresh, SBS, button, SDK display-control or optical-fit changes. High Hz and 3D are not first-picture prerequisites.
2. Record exact identity, SDK/firmware, build, output geometry, active **44.5 diagonal / upstream TBD** and whether any fallback was used. Unexpected fallback46 or inherited Luma52 must be a failed route-preflight check, not an unnoticed substitution. No correction stage; no new optical-pitch claim. Keep settings session-local; do not overwrite per-model saved settings.
3. First picture: central readable card/grid with straight lines and square/circle references; comfortable center, full usable region, no double image or gross stretch. Recenter once using existing semantics. A consistently offset screen is not permission to invent a pitch correction. Cody may stop at any time for strain, nausea, double vision or unexpected motion; do not continue to obtain a calibration number.
4. Only if the still picture is comfortable, ask for small slow left/right and up/down motions and a return to center. Confirm expected direction and tolerable world stability. Success means a minimum viable comfortable visual test, **not** optically calibrated production support. Use the parent's existing staged stop/cleanup route; no activation commands are supplied here.
5. If projection scale appears wrong while axes/timing remain healthy, allow an explicit, logged, session-only scalar diagonal override in a proposed **44.5–50.0° inclusive** test range, in at most 1° steps (final fractional step allowed). This is an engineering test bound, not a measured device limit. Re-evaluate one setting at a time; do not sweep automatically. Compare 46 and then up toward50 only if comfort improves. Require finite numeric input, reject bool/NaN/infinity and out-of-range values; label every override `provisional-user-test`. Recompute aspect from 1920×1080; never compensate by stretching one axis or changing resolution. Parent/integrator own implementation and consent, not this document.
6. If no comfortable setting exists in that range, end the trial and retain observations. Do not unlock 56.295, widen bounds, tune pitch or weaken the production guard automatically. Next focused work can estimate angular scale against independently measured head rotations and observed screen displacement, separating center-pose offset from scale; it need not wait indefinitely for vendor documentation. Subjective tuning may select a comfortable provisional value but cannot establish a physical calibration.

## Offline executable acceptance

This standalone Python block tests calculation consistency, explicit test bounds and source semantics at the integration baseline. It touches no device, IPC, compositor or GPU and installs nothing. It does not test or claim the integrator's future runtime guard/override implementation. Run from repository root by extracting this block, as shown below it.

```python
import math
from pathlib import Path
import unittest

A = 16 / 9

def angles(d):
    t = math.tan(math.radians(d / 2))
    x, y = t * A / math.sqrt(1 + A*A), t / math.sqrt(1 + A*A)
    return math.degrees(2*math.atan(x)), math.degrees(2*math.atan(y)), x, y

def override_ok(v):
    return type(v) in (int, float) and math.isfinite(v) and 44.5 <= v <= 50

class ProjectionAcceptance(unittest.TestCase):
    def test_seed_and_alternatives(self):
        for d, h, v in ((44.5,39.249517,22.682792), (46,40.605104,23.511341), (50,44.235848,25.754484)):
            hh,vv,x,y = angles(d)
            self.assertAlmostEqual(hh,h,places=6)
            self.assertAlmostEqual(vv,v,places=6)
            self.assertAlmostEqual(x/y,A,places=12)
            self.assertAlmostEqual(math.degrees(2*math.atan(math.hypot(x,y))),d,places=12)
    def test_horizontal50_is_not_diagonal50(self):
        d = math.degrees(2*math.atan(math.tan(math.radians(25))*math.sqrt(1+A*A)/A))
        self.assertAlmostEqual(d,56.295070,places=6)
        self.assertAlmostEqual(angles(d)[0],50,places=12)
        self.assertFalse(override_ok(d))
    def test_marketing_consistency_is_conditional(self):
        self.assertAlmostEqual(math.degrees(2*math.atan(146*.0254/8)),49.740162,places=6)
        self.assertAlmostEqual(1920/43,44.651163,places=6)
    def test_test_bounds_fail_closed(self):
        for v in (44.5,45.5,46,49.5,50): self.assertTrue(override_ok(v))
        for v in (True,None,'50',44.49,50.01,float('nan'),float('inf')):
            self.assertFalse(override_ok(v))
    def test_renderer_semantics_and_fallback(self):
        w = Path('spike/src/wayland.cpp').read_text()
        v = Path('spike/src/video.cpp').read_text()
        self.assertIn('published.value_or(46.f)',w)
        self.assertIn('diag * M_PI / 360.0',w)
        self.assertIn('half_w = float(d * a / std::sqrt(1 + a * a))',w)
        self.assertIn('half_h = float(d / std::sqrt(1 + a * a))',w)
        self.assertIn('double(diagonal_deg) * M_PI / 360.0',v)
    def test_size_change_direction(self):
        # Fixed world x projects to NDC x/half_w. Larger D -> smaller image.
        self.assertLess(.1/angles(50)[2], .1/angles(44.5)[2])

suite = unittest.defaultTestLoader.loadTestsFromTestCase(ProjectionAcceptance)
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
```

Reproduction (standard Python only):

```sh
python - <<'PY'
from pathlib import Path
text = Path('docs/pro2/projection-policy.md').read_text()
code = text.split('```python\n', 1)[1].split('\n```', 1)[0]
exec(compile(code, 'projection-policy-acceptance', 'exec'))
PY
```

Executed offline on 2026-11-04 after fast-forward to `1d141f8`: **6 tests passed**, exit 0; `git diff --check` clean. The old display commit `8d51172` remains an ancestor. This change owns only this policy file; no runtime/controller, device, display, SDK loading, IPC or GPU action occurred. Integrator owns runtime implementation and executable runtime acceptance; this mathematical/source check is not a substitute for it.
