# Presentation output placement default

Every settings-panel XR session parks the detected physical presentation output
before creating the three virtual desktops. This applies to both glasses models,
all viewing modes, and either cursor-follow setting. No personal Hyprland monitor
rule is required and there is no opt-in toggle.

The presentation output stays enabled for rendering. Its logical right edge is
placed at the left edge of the other physical displays, and its top is 8192 logical
pixels below their lowest edge. Consequently it neither extends the right edge
used to place XR-1 nor sits between the interactive desktops. Existing XR outputs
are excluded from this calculation so configuration reloads are idempotent.
Resolution, refresh, scale and transform are preserved and placement is read back
before launch proceeds. A missing independent desktop or failed readback aborts
startup rather than launching with an obstructed desktop row.

Configuration reloads reapply the policy during the session. Placement is runtime
only and remains after stopping, matching the existing runtime display-mode policy;
a reload outside a session or logout restores the user's configured placement.
This is not a compositor-wide ban on manually focusing the presentation output.
The historical presentation-only spike and isolated VM trials retain their existing
layout policies.

Regression coverage: `tests/native_spike/test_presentation_layout.py` and the
settings-panel runner tests, including Beast with its optional mode switch off.
