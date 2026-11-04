# RQ6 real first-launch blocker — offline root cause and correction

Parent's authorized first test reported pluginpinrejection before pluginload/rendering. SDKowner had started, then closed cleanly; no leftovers/displaychange perparent. No retry or hardware action bylead. No private SDKoutput/poses/serials read or exported.

Actual root `/home/cody/.hermes/profiles/coderbotxr/cache/scratch/pro2-release-review-z3nsa4vu/pro2-runtime-release/runtime` was fully pinned. Legacyregex supports hyphens but requires a different installeddirectory suffix. Offline actualroot verification + real BundleClient.plugin_load (requestspy only) reproduced ValueError with zero compositorrequests. Lead redtests also reproduced a whitespacepath reachingclientconstruction tooearly. RED log here.

Fix42416e7 splits supervisedbundle lexicalcanonicalpath grammar from normalinstalledtopology; exact suffix+manifesthash remain. `verify_manifest` performs same plugincheck before client/SDK; each actualload/unload revalidates pin. Symlinks/aliases/unsafecharacters changedpins reject. Packager checks destination before compiler; packaged `--preflight` independently validates all paths/hashes without consent/state/SDK/client. NormalPLUGIN_PATH is unchanged.

Lead4pathcases green, including realrequest dispatch and syntheticowner teardown on actualpluginrequestrejection; runtimepipeline10pass;534controller/8normalguards pass. IndependentQA also reproduces exactroot and realinertfilehash path plus adversarial aliases/symlinks/preflight. New finalartifact must be rebuilt and source/hash/preflight audited after this fix; old92archive is not test-ready. Completion means correctedofflineartifact, not physicalretry or successfulXRpicture.
