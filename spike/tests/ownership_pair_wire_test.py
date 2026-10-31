"""Consumer-shaped JSON through the exact native request validator, offline."""
import json
import subprocess
import sys
from typing import Any

hello: dict[str, Any] = dict(schema=2, session="guest", run_id="run", request_id=1,
             epoch="unknown", op="pair_hello",
             payload=dict(mode="isolated-vm", target_name="Virtual-1", fallback_name="Virtual-2"))
observe = dict(hello, epoch="a" * 48, op="pair_observe",
               payload=dict(mode="isolated-vm", target="b" * 48, fallback="c" * 48))
legacy = dict(hello, schema=1, op="hello", payload=dict(fallback_name="DP-1"))
cases = [(hello, True), (observe, True), (legacy, True),
         (dict(hello, schema=1), False), (dict(legacy, schema=2), False),
         (dict(observe, payload=dict(observe["payload"], target="b" * 49)), False),
         (dict(hello, request_id=2**64 - 1), False),
         (dict(hello, payload=dict(hello["payload"], mode="normal")), False)]
uuid = "12345678-1234-1234-1234-123456789abc"
spec = dict(id=uuid, name="XR-1", index=0, width=1920, height=1080, refresh_millihz=120000, scale=1, x=1280, y=0)
iso = dict(observe, op="create", payload=dict(spec, mode="isolated-vm"))
iso_inspect = dict(observe, op="inspect", payload=dict(id=uuid, mode="isolated-vm"))
iso_remove = dict(observe, op="evacuate_remove",
                  payload=dict(id=uuid, name="XR-1", fallback="c" * 48, mode="isolated-vm"))
cases += [(iso, True), (iso_inspect, True), (iso_remove, True),
          (dict(iso, schema=1), False),  # mode field never accepted by schema 1
          (dict(iso, payload=spec), False),  # schema 2 lifecycle requires mode
          (dict(iso_inspect, payload=dict(id=uuid, mode="normal")), False),
          (dict(iso_remove, payload=dict(iso_remove["payload"], fallback="C" * 48)), False),
          (dict(observe, op="hello", payload=dict(mode="isolated-vm", fallback_name="Virtual-2")), False),
          (dict(iso, payload=dict(spec, mode="isolated-vm", name="XR-SPIKE-run-0")), False),
          (dict(iso, payload=dict(spec, mode="isolated-vm", workspace=11)), False),
          (dict(iso, payload=dict(spec, mode="isolated-vm", index=1)), False)]
for request, accepted in cases:
    result = subprocess.run([sys.argv[1], "--validate"], input="xrspike " + json.dumps(request),
                            text=True, capture_output=True, timeout=5, check=False)
    assert result.returncode == (0 if accepted else 2), (request, result)
print(f"{len(cases)} Python-generated native wire cases passed")
