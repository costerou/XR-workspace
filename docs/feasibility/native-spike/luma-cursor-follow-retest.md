# Luma Ultra cursor-follow retest — 2026-11-01

## Physical evidence

Source: Cody's physical test, reported by the user in the debugging session on
2026-11-01 (America/Detroit). This is human-reported visual evidence, not an
agent-observed display capture or a measured performance result.

After the controller startup-state fix, Cody:

1. Reopened the XR app with the Luma Ultra connected.
2. Started XR and waited for tracking calibration.
3. Played YouTube fullscreen with cursor follow enabled.

Reported result: “Everything is working fine with no blackout.” This passes the
reported Luma Ultra reproduction scenario. Before starting XR, Cody also confirmed
that a normal desktop was visible through the glasses.

## Diagnosis and fix

The controller previously reported `run unit alive but control socket unavailable`
while the runner was still starting/calibrating the tracking driver, before the
renderer and its socket existed. That message did not establish video loss.
The panel could also retain an old local error, and startup had no cancel control.

The fix publishes runner startup progress, reports calibration as startup rather
than a renderer-control failure, distinguishes loss of an existing run's control
socket, permits stopping startup/failed live runs, preserves the stopping state,
cleans up the owned tracking driver on interrupted calibration, and clears old
panel errors on reopening/state updates.

Earlier investigation observed a visible compositor capture of the glasses output
while Cody reported physically black glasses, plus a USB disconnect/reconnect and
subsequent driver USB command failures. Whether that disconnect was the trigger or
a recovery attempt was not established. The original physical blackout's root
cause remains unproven; this retest establishes successful operation after the
state-handling fix, not causation or a general hardware recovery guarantee.

## Scope

No new presentation-rate, latency, sustained-duration, reconnect, Beast, or
cross-device acceptance claim follows from this test. Earlier failed panel
attempts in [physical-panel-acceptance.md](physical-panel-acceptance.md) remain
historical evidence; this successful later retest does not rewrite them.
