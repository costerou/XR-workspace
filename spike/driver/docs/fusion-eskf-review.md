# Fusion review: Mahony against an error-state Kalman filter (D11)

Result: **not adopted. Mahony (`luma::Fusion`) stays the shipped filter.**

The gate in `docs/rust-rebuild-plan.md` (D11) was "adopt only if tilt p95 or drift improves on
all fixtures". The candidate did not improve either metric on every fixture, so it fails the gate.
Nothing in the shipped driver changed. The candidate lives only in the review tool.

## What was compared

- **Reference:** `luma::Fusion` (`include/luma/fusion.hpp`, `src/fusion.cpp`) with its default
  `FusionConfig`.
- **Candidate:** `luma::eskf::Filter` (`tools/eskf.hpp`). It is an error-state Kalman filter with
  six error states: the attitude error (body frame, right-multiplied) and the gyro bias.
  - It predicts with the same Savage rotation-vector step and coning term as `Fusion`, so any
    difference comes from the correction, not the integrator.
  - Its gravity update uses `H = [[u]x, 0]`. The noise grows with the deviation of |a| from 1 g
    and with the turn rate. The update is skipped above a 10 % |a| deviation or 1 rad/s.
  - Its zero-rate update (ZARU, `H = [0, I]`) runs per 100 ms block. It uses the same "still" and
    "quiet" block tests as `Fusion` and a chi-square gate.
  - It uses a Joseph-form covariance update.
  - It reports calibrated when every bias sigma is under 0.5 °/s, or at 2.5 s, which matches
    `Fusion`'s Coarse timeout.

There is no magnetometer, so neither filter can observe heading. "Drift" here means how well each
filter holds heading through its bias estimate.

## Tool

```
luma_fusion_review REAL.bin UPSIDE_DOWN.bin [--quick] [--set name=value ...]
```

- The tool runs both filters sample for sample over each fixture and prints one row per fixture.
- It then prints the verdict: `adopt` or `not adopted, Mahony stays`.
- `--set` overrides any `eskf::Config` field, which is how the tuning sweep below was run.
- `--quick` shortens the long simulated scenarios from 10 min to 2 min. The ctest entry
  `luma_fusion_review` runs `--quick`.
- The exit code is 1 only when a fixture produces no score or a non-finite score. A "not adopted"
  verdict is a valid result, not a failure.

## Fixtures and metrics

The simulated fixtures come from `tests/imu_sim.hpp` and have ground truth:

- still on a desk
- still while the bias warms up
- worn and resting while warming
- worn desk work while warming, with a lever arm
- restless head (300 s)
- fast yaw at 700 °/s
- three-axis coning
- head shake with a lever arm

For each one, the tool computes two numbers:

- **Tilt p95:** the 95th percentile of the angle between true and estimated up. It is sampled every
  10th sample and starts after the settle time, once both filters report calibrated.
- **Drift:** the change in yaw error from the start of scoring to the end, in degrees per minute.

The real fixtures are `tests/fixtures/luma_imu_real.bin` (8 s, worn, with a 60° turn) and
`luma_imu_upside_down.bin` (2 s). They have no ground truth and are too short for
`luma_replay_report`'s 5 s windows, so the tool scores rest blocks instead. A rest block is a
100 ms block with gyro std under 0.015 rad/s and |a| within 5 % of g.

- **Tilt:** the angle between the filter's up and the block's mean accelerometer direction.
- **Drift:** the absolute heading slope over rest runs of at least five blocks, weighted by run
  duration.

## Fairness steps

The candidate was tuned so that the result is not a strawman:

- It uses the same integrator and the same stillness tests as `Fusion`.
- A first version accepted tangential lever-arm acceleration in the head-shake fixture and gave
  3.4–4.4° tilt. Tighter accel gating and rate-dependent noise fixed that.
- A sweep of 144 configurations over `accel_gate`, `accel_norm_k`, `quiet_sigma`, `bias_walk`,
  `accel_sigma` and `accel_rate_gate` was run. Gating grids and `quiet_sigma`/`zero_rate_chi2`
  variants followed.
- No configuration gave `adopt`. Every configuration lost on at least one fixture for tilt and on
  at least one fixture for drift.

## Results (full length, default candidate config)

| Fixture | Tilt p95 Mahony (°) | Tilt p95 ESKF (°) | Drift Mahony (°/min) | Drift ESKF (°/min) |
|---|---|---|---|---|
| sim still desk | 0.024 | 0.034 | 0.003 | 0.003 |
| sim still warming | 0.067 | 0.574 | 0.320 | 0.014 |
| sim worn rest warming | 0.397 | 0.402 | 0.926 | 0.300 |
| sim worn desk work | 0.422 | 0.451 | 1.518 | 1.405 |
| sim restless head | 1.902 | 0.054 | 0.201 | 0.702 |
| sim fast yaw | 0.046 | 0.035 | 0.363 | 0.574 |
| sim coning | 0.071 | 0.141 | 0.429 | 0.809 |
| sim head shake | 0.084 | 0.085 | 0.413 | 0.741 |
| real worn 8 s | 2.063 | 0.689 | 3.824 | 3.951 |
| real upside down 2 s | 0.018 | 0.016 | 0.036 | 0.116 |

Verdict: tilt p95 improves on 4 of 10 fixtures and drift improves on 3 of 10. The gate needs one
metric to improve on all 10, so the candidate is **not adopted**.

## Leads for later tasks

The candidate is not better overall, but it shows two strengths that later tasks can use without
replacing the filter:

- **Early and restless tilt.**
  - What it showed: ESKF tilt was far better on the restless head fixture (0.05° against 1.9°) and
    in the first seconds of the real worn recording (0.7° against 2.1°).
  - Cause: Mahony runs at Coarse gain just after calibration, and no still window arrives when the
    head never rests.
  - How to use it: a covariance-like confidence that raises the gravity gain until tilt is known
    would capture most of this inside `Fusion`.
- **Bias held in state with ZARU (D6, D7).**
  - What it showed: ESKF drift was far better while still and warming (0.014 against 0.32 °/min)
    and while worn and resting (0.30 against 0.93 °/min).
  - How to use it: the bias work in D6/D7 should test a per-block zero-rate update with an explicit
    bias variance against the current tau-based tracker.
- **Where ESKF lost.** It drifts more under sustained motion (fast yaw, coning, head shake),
  because its gravity update feeds yaw-coupled bias error through the covariance. Mahony's
  integrator sees only the two tilt axes.

The real fixtures are 2 s and 8 s long. A long real recording, such as the 20 min one in
`capture-findings.md` (which is not in the repo), would give a stronger real-world drift number.
Rerun with `luma_fusion_review` if one is added to `tests/fixtures/`.
