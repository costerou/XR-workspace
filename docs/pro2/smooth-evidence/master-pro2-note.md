# For the orchestrator: master lets the Pro 2 start, unguarded

`origin/master` 0b43593 has no `pro2` in `tools/xr_controller/detect.py` or in `MODELS`. A Pro 2 reads as `unknown`, `identify` accepts its `VITURE` connector, `begin_start` works, and the run uses `/usr/bin/xrDriver`. That is what happened at 22:36:22 and again at 22:46 on 2026-11-06. Nothing here changes master.

What that session does not have: a Pro 2 profile (it uses `unknown`'s), the SDK hash and firmware pins, the supervised consent, or the mount and level work. Its FOV and pitch are upstream's. It is not known to be harmful: it ran 9.5 minutes and recovered. Whether its two DP-2 disconnects at 22:45:54 and 22:46:11 relate to it is unknown.

| Option | Change | For | Against |
| --- | --- | --- | --- |
| **A. Guard now, route later (recommended)** | Only the `pro2` identification and refusal from pro2-integrate, as a small fix to master. Start says the Pro 2 is not supported yet | Safest, small, the same refusal already tested on this branch. Stops an untested path by accident | Cody loses a working Start until the route lands |
| B. Merge this branch and enable the route | Whole branch, then `pro2-route enable` | One step, the intended end state | The mount pitch is not settled. Too much to merge on an open question |
| C. Leave it | Nothing | Cody can use the Pro 2 today | An unpinned path with upstream's settings, and reports that are easily mistaken for the bridge |

Recommendation: A, with a plain message, unless Cody prefers to keep the XRLinuxDriver path working until the route is ready. That is his call: it works for him today, and A takes that away.
