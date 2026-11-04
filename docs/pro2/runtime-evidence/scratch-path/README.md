# Long scratch-root harness regression

Parent's independent rerun of the genuine CLI matrix failed before fixture readiness under `TMPDIR=/home/cody/.hermes/profiles/coderbotxr/cache/scratch`. This invalidates any implication that the earlier short-temp-root pass established acceptance in every environment. It did not indicate a real SDK or hardware failure; the parent performed no activation.

The integrator independently reproduced the environment failure. Captured fake compositor stderr showed `OSError: AF_UNIX path too long` at the fixture's absolute-path bind. Even before adding the runner's namespace nesting, the longest socket address was 137 bytes; Linux pathname Unix sockets allow at most 107 non-NUL address bytes. The fixture also launched bind operations in threads and wrote fake-ready without waiting for them. Thus readiness could either fail or incorrectly precede dead listeners, obscuring startup errors. An initial diagnostic run surfaced the bind error; the strengthened socket-readiness regression rejects the old compositor before any controller launch. Both RED outputs are retained here.

The fix is test-only:

- The fake compositor changes cwd to its own private runtime directory and binds relative names (the longest normal fixture name is 67 bytes). The filesystem sockets remain in the same scratch directory, with the same peer process and real client validation. No /tmp alias, symlink, abstract/host IPC, shorter fabricated instance, production transport change or pin relaxation is used.
- All three listeners bind synchronously before fake-ready. Cases assert every socket exists and deliberately exercise absolute socket paths longer than 107 bytes.
- Failure output includes the fixture's stdout/stderr. The combined auditor preserves nested matrix failure output rather than hiding it in CalledProcessError.
- The runner and combined auditor use explicit private scratch under TMPDIR; when TMPDIR is unset, they use this checkout's `build/pro2-cli-scratch`. The runner gives every child a private nested TMPDIR and runtime inside fresh user/IPC/network namespaces. D-Bus remains nonexistent/private, displays fake, device/library/socket audits intact.

The actual parent's bundle and command were re-audited with this updated test checkout:

```
TMPDIR=/home/cody/.hermes/profiles/coderbotxr/cache/scratch \
python3 tests/pro2_runtime_qa/audit_extracted_bundle.py \
 /home/cody/.hermes/profiles/coderbotxr/cache/scratch/pro2-cli-review-cdzt7q5y/pro2-runtime-release/runtime \
 --source 9158428f8daeb19fb8a8dbe28358c43252cb3c2b --genuine-cli-matrix
```

Result: all 330 pins/source/canonical entry/preflight/dispatch checks passed; 4 genuine CLI cases passed, zero skips, 11.478s; final hashes unchanged. The same combined audit with TMPDIR unset passed from repository scratch, 4 cases, zero skips, 11.505s. The 42 independent QA cases also passed under the parent's scratch root, 12.236s. These are integrator reruns of the existing independent tests; the parent still needs to confirm the rerun in its own audit checkout.

Runtime source stays `9158428f8daeb19fb8a8dbe28358c43252cb3c2b`, manifest SHA256 `088343e0ceca1f10def4042baad3ac64db26f75aca3aabb0d1a9f763762192cb`. The shipped archive stays byte-for-byte unchanged: SHA256 `3b0d258976428c9a4b084b5409f822163844e97fcfa6dce2079e98b6a6e440f3`. No rebuild, new runtime archive, installation or physical retry is required by this harness fix. The review/tests copy inside the existing archive is historical; use the updated repository tests for the audit command.

This proves the two tested scratch configurations, not every filesystem/environment. Existing ASCII/canonical/no-symlink bundle-location rules and namespace/compiler prerequisites still apply; filesystem path/component limits remain. Physical XR, compositor Lua/GPU behavior and projection comfort remain unproved. No real SDK, device, live IPC, GPU, controller/service action, /tmp fixture creation or private parent SDK-log read occurred during this follow-up. Parent alone decides any later separately consented physical retry after its own matrix passes.
