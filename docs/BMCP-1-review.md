# BMCP-1 host review

Reviewed against upstream `febcd90ece59c3416d7c55fd3723cea46bcaa59d`.
This is implementation-agent source review, not an independent review or native
acceptance. No Blender, listener, UI input, native emitter or active configuration
was operated. The local editable package and wheel were built in this checkout's
isolated environment only.

## Findings resolved before draft publication

- Default tool exposure changed upstream behavior: extension registration now
  requires `BMCP_ENABLE_CONTROL=1`; upstream surface tests pass.
- Derived radius policy could exclude configured scene-space size: SCENE radius
  now participates in strict state binding, while both raw values are retained.
- Pen acquisition could occur outside SCULPT mode: explicit refusal added.
- Window movement was not bound: raw window geometry is now part of the hash.
- Process exit alone could unlock a missing-release job: both zero-pressure up
  and proximity-exit emitter observations are now required, otherwise UNKNOWN
  retains the lease. Native receipt remains UNKNOWN even with these observations.
- Lost release acknowledgement could clear local ownership: all release paths
  now require an explicit acknowledgement.
- External review during worker completion could be overwritten: attachment
  waits until the job settles, and cancellation state is retained at completion.
- Extension dispatch now explicitly refuses a non-main thread or non-loopback
  listener. This does not secure upstream raw socket execution or prove a crash fix.

## Verification

- 30 extension host tests pass, including public MCP execute -> durable
  coordinator -> injected external backend, cancellation, exact/conflicting IDs,
  restart without replay, partial/absent evidence, lease ownership, wrong target,
  frame/schema errors, path/hash guards, capture/save errors, explicit restore,
  actual state-hash behavior and configured/derived radius distinction.
- Wheel build succeeds. Wheel addon and all three new host modules were compared
  byte-for-byte with source. Root and bundled addon are identical.
- Full final suite: **584 passed, 6 failed, 1 skipped** (30.81 seconds).
- Six upstream compatibility failures reproduce against a `git archive` export
  of baseline source using the same isolated interpreter. Every failure is
  `test_addon_status_always_answers`: `No module named 'blender_mcp.config'`.
  That module is absent from the tracked baseline and is gitignored. No private
  config or secrets were read/copied to mask the failures.
- `git diff --check` passes. MIT notices are unchanged.

Addon SHA256:
`c6364c6f2e311b31b218fe3dbffaadd4ae9ec7b8c88521015d41e85e872f1555`

Local wheel SHA256 (build artifact, not uploaded):
`ba61761e5ecaf0268d3a2b572d70515a47c517f756748dca5ce1440fb9c258b8`

## Open boundaries

All native acceptance gates in the integration packet remain pending. In
particular, RNA readback is not proven synchronized with live sculpt geometry;
reported state is not exhaustive; viewport-to-Quartz calibration is external;
UI state can change after acquire; capture fidelity/stability and save/reopen
have not run natively. The executor is the existing externally approved binary,
not a newly shipped native implementation. External evidence reviews retain
caller provenance and never silently become machine receipt/effect PASS.

On coordinator process death, a separately running bounded emitter may continue
until it exits; restarted jobs stay UNKNOWN and block replay. Parent must verify
emitter exit and native release before reconciliation. A restarted Blender or
changed file/object invalidates old session recovery; checkpoint manifests are
session-local. No transactional Undo or safe-mode socket security is promised.
