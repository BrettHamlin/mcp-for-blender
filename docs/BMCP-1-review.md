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

## Independent review repair round

All four reported findings are addressed in this implementation round. This is
an implementation response, not the next independent review or native acceptance.
The supplied `/tmp/bmcp_independent_repro.py` was read. New regression tests were
run before fixes: 14 failed and 6 passed, reproducing all four finding classes.

1. **Old reconciliation releasing a new lease:** each job now commits a random
   acquisition UUID to SQLite before dispatch. Acquire/release use the exact
   session/request/acquisition tuple. The addon remembers retired tuples for its
   instance lifetime: duplicate release acknowledges only that retired owner and
   never touches a newer lease; duplicate active acquire acknowledges ownership;
   retired acquire refuses replay. Two stores using `one` in the same session
   receive distinct owners. The actual extracted addon/coordinator regression
   includes a lost release acknowledgement and coordinator reopen. Legacy records
   lacking a UUID remain unresolved and cannot guess ownership.
2. **Post-launch metadata failure losing custody:** metadata write exceptions
   stay within the process supervision loop. They trigger bounded SIGTERM cleanup,
   polling and evidence parsing. Poll/signal errors and the 20+5 second timeout
   remain bounded without SIGKILL. Exit alone cannot release the lease. Tests run
   the actual adapter through the coordinator with a fake Popen and failing
   `process.json`, both with and without emitter up/exit observations. Error
   outcomes remain UNKNOWN even when cleanup permits releasing the lease.
3. **Synchronous MCP wrappers blocking cancellation:** all extension handlers are
   async and offload blocking host work with `asyncio.to_thread`. Existing socket
   serialization and the Blender main-thread queue remain intact. Reconciliation
   releases the coordinator lock before its socket wait, retaining the durable
   unresolved lease. Public FastMCP tests verify timer progress and status/cancel
   completion while inspect or reconciliation is blocked.
4. **Safety boolean coercion:** both `discard_current` and
   `native_release_verified` use Pydantic StrictBool. Public FastMCP dispatch tests
   reject 1, 0, strings and null before reaching either handler operation, and
   check that literal true/false route unchanged.

Additional host cases cover duplicate/retired/unknown owners, legacy records,
partial startup, external cancellation at launch, signal/poll errors and timeout.
No process other than isolated host fakes was signalled by the tests.

## Verification

- 58 extension host tests pass, including public MCP execute -> durable
  coordinator -> injected external backend, cancellation, exact/conflicting IDs,
  restart without replay, partial/absent evidence, lease ownership, wrong target,
  frame/schema errors, path/hash guards, capture/save errors, explicit restore,
  actual state-hash behavior and configured/derived radius distinction.
- Wheel build succeeds. Wheel addon and all three new host modules were compared
  byte-for-byte with source. Root and bundled addon are identical.
- Full final suite: **612 passed, 6 failed, 1 skipped** (32.26 seconds).
- Six upstream compatibility failures reproduce against a `git archive` export
  of baseline source using the same isolated interpreter. Every failure is
  `test_addon_status_always_answers`: `No module named 'blender_mcp.config'`.
  That module is absent from the tracked baseline and is gitignored. No private
  config or secrets were read/copied to mask the failures.
- `git diff --check` passes. MIT notices are unchanged.

Addon SHA256:
`d0aeb7cfdeb8f24b8529b2c40d2d62e3a4390870999be2fd0e6ebb97b4ba82ee`

Local wheel SHA256 (build artifact, not uploaded):
`b1b8cdc799abafb470db62341c5189b771886e96f74b829acf14e7a9f8397086`

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
