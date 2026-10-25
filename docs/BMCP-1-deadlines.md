# BMCP-1 absolute emission deadlines: response to pen451/454

Source/inert implementation only. P1/P2 are addressed in the fork; acceptance requires the parent's combined native/fork/447 caller review. No native delivery or sculpt effect is established.

## Public recipe and clocks

`pen_preview` and `pen_execute` accept the existing `bmcp-pen/v1` recipe with an optional frozen `emission_deadlines` object:

```json
"emission_deadlines": {
  "fixture_posting_cutoff_mach_seconds": 420.0,
  "assignment_expires_unix": 1100.0
}
```

These numbers are illustrative, expired test values, not an assignment. Both must be finite positive JSON numbers; bool, strings, null, missing members and extra members are rejected. The object is immutable after validation. Preview canonicalizes and hash-binds both values, and the coordinator persists that recipe before remote acquisition. Same-ID deduplication cannot extend either deadline. Omitted/null optional objects normalize to the previous recipe representation, preserving legacy hashes and v1 behavior. A bound recipe requires the application backend; there is no CLI/unbound fallback.

The caller captures the original fixture Mach origin once and adds 420 once, and supplies the ORIGINAL assignment's Unix expiration. `pen_deadlines.mach_seconds()` checks live Python `get_clock_info('monotonic')`: implementation must equal `mach_absolute_time()`, monotonic must be true, adjustable false. Otherwise it refuses. It never converts a later remaining interval or assumes a Dispatch epoch. This boot's Mach clock excludes sleep; assignment time uses `time.time()`. Equality expires. Each authority gate latches observed expiry, and every expiry branch terminates that job's emission path, so wall rollback cannot revive it. Restarted pending/running jobs remain UNKNOWN and are not replayed.

## P1: asynchronous delay no longer renews the posting window

Checks occur at coordinator admission, worker entry, after exact lease acquisition, backend entry, immediately before launch, while awaiting launch/READY, after authenticated READY, before intent, inside the coordinator's intent lock, and immediately before AUTH. Channel writes recheck after the writable wait and before each send. The existing 10/30 second channel budgets remain in force simultaneously; the five-second exit-observation budget is unchanged.

The actual PenJobs and AppQuartzBackend regression enters at Mach10, returns from acquire419, receives READY421, and emits no AUTH or intent. It cancels through the authenticated cleanup path, observes the retained fake application's exit, and releases only the exact acquired UUID lease. There is no fixed admission-margin assumption and no modification to the existing connection lock or per-chunk timeout.

## P2: original assignment expiry survives every asynchronous boundary

Tests expire the original Unix authority during acquisition, READY and intent persistence. New authorization is refused with `original assignment expired`; fixture expiry uses `fixture posting cutoff expired`. Same-ID attempts to extend deadlines conflict with the durable original hash. Lost acquisition acknowledgement remains UNKNOWN with the lease held. Known expiry before acquisition creates a durable refusal without a lease. Known expiry after acquisition releases only the acknowledged exact UUID; lost release acknowledgement retains UNKNOWN through the existing coordinator behavior.

## Custody and wire behavior

Bound requests use `software-pen-custody/v2`, the original 14 fields plus the two unchanged values at top level. Request SHA binds all bytes; recipe SHA binds the original nested authority object. READY/AUTH/CANCEL/FINISHED keep their exact keysets and bind v2. Unbound requests remain v1 with 14 fields. Existing actual application identity, dynamic kernel peer and controller self-audit checks, main-host Cocoa loop, exact lease reconciliation, no replay and no PID fallback are preserved.

Emission expiry does not gate CANCEL, FINISHED, retained-app exit observation, or lifetime supervision. Confirmed preauthorization expiry plus actual retained-app exit can release the known lease without claiming native receipt. After an AUTH attempt, uncertainty stays UNKNOWN. A complete, hash-verified native expiry receipt plus observed exit permits lease release only with sender release evidence or an explicit unauthorized/zero-post result; the job outcome remains UNKNOWN. Incomplete evidence or unverified termination retains custody. Cancellation of the public caller's await does not revoke the worker or drop its retained application.

## Exact native contract pins

Read-only source in the original checkout, verified against the parent handoff:

- `docs/tools/software-pen457-deadline-contract.md`: `9ebf89ebd64997369d097a6441ef61460b39380208e13d2c0bb46e43c888fa26`.
- `docs/tools/software-pen-custody-v2.schema.json`: `b1689a79b0335b9fd38b201e6984a03191addf7a4bf606d6b3c29eeb989ec5a1`.
- Final native source pin reported by owner457: `a27268b4827eee3ed45bf9ad16182d85c5a3b9be0f2329f11cd38dca972d2f0b` (native source/tests owned and assessed separately).

The original frozen v1 schema was not edited. The read-only457 clock proof brackets direct Mach/timebase samples between Python samples. The fork's existing Python3.11 environment independently reported `mach_absolute_time()`, monotonic=true, adjustable=false, resolution `4.166666666666667e-08`; execution still checks live metadata rather than trusting that observation.

## Verification and remaining gates

Run `.venv/bin/python tests/run_bmcp_deadlines_inert.py` from this worktree. Result: **111 passed, 1 deselected**. It blocks real socket creation, bypasses package server initialization, uses actual methods with inert app/transport boundaries, and covers strict types, immutable hashes/durable values, timing failures and timely controls, v1 compatibility, exact UUIDs, reconciliation, receipt verification, retained callbacks/main-loop stall behavior, interrupted public awaits, cleanup after expiration and unresolved lifetime retention. The existing socket-backed MCP integration test is deliberately deselected; socket/app tests are not represented as run. All five changed/new Python modules source-compile. Root/bundled addon bytes remain identical at SHA256 `d0aeb7cfdeb8f24b8529b2c40d2d62e3a4390870999be2fd0e6ebb97b4ba82ee`; no addon/package dependency changes were needed.

No Cocoa/app/native process, IPC/socket/listener, Blender/input/TCC/signing/install or dependency operation occurred. No original checkout files were modified. Existing c25 and437 d55 candidates remain unchanged and cannot claim v2 enforcement. Parent must complete one combined review including459's original caller-bound values, then separately assess any new candidate identity/access before native handoff. Actual public READY/AUTH/FINISHED, native release/receiver/application effects, owned cleanup and normal custodian exit remain unproved. 435 inert PASS and442 bounded component acceptance remain separate; whole-art FAIL/0of42 remains unchanged.
