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

These numbers are illustrative, expired test values, not an assignment. Both must be positive finite JSON numbers; bare integers are restricted to `1..2**63-1` (signed 64-bit), while floats may span the positive finite Double range; bool, strings, null, missing members and extra members are rejected. The object is immutable after validation. Preview canonicalizes and hash-binds both values, and the coordinator persists that recipe before remote acquisition. Same-ID deduplication cannot extend either deadline. Omitted/null optional objects normalize to the previous recipe representation, preserving legacy hashes and v1 behavior. A bound recipe requires the application backend; there is no CLI/unbound fallback.

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


## Pen460 initial P2 response: float conversion alone (superseded below)

The combined review confirmed original451 P1/P2 at the source/inert boundary, but found that Python arbitrary-precision integers such as `10**400` survived the public strict-number model and reached a native parser that rejected their JSON representation. Its exact producer bytes, parser failure and conservative UNKNOWN lifecycle evidence were read from `/private/tmp/pen460-combined-deadline-review`; those review artifacts remain unchanged.

`EmissionDeadlines` now validates BOTH fields by testing conversion to a finite Double-compatible Python float, rejecting conversion overflow and nonfinite results. The validator returns the original value rather than the converted float: accepted integer/float types, canonical bytes and recipe hashes remain unchanged. Existing strict bool/string, positivity and frozen-object checks remain. Invalid values fail typed public Recipe validation, preview and coordinator execute before backend validation, acquisition, durable job creation or any native attempt. No UNKNOWN/release behavior was weakened; clock, coordinator, custody, wire and native implementation are unchanged.

Verification: **139 passed, 1 deselected** via the existing inert runner (the original111 plus28 both-field rejection/boundary cases). New cases cover `10**400`, `10**309`, `2**1024`, positive/negative infinity and NaN on each field; accepted controls cover ordinary integers/floats, `10**308`, the largest finite Double and its integer form, and the smallest positive subnormal Double. Tests assert exact original value/type/canonical/hash preservation and zero job/acquisition/backend activity for rejected input. The legacy v1 hash is explicitly unchanged. Source compilation, root/bundled addon parity and diff whitespace checks pass. No excluded socket test, native parser execution, app/Cocoa/input operation or dependency change was performed.

Only `pen.py`, `test_bmcp_deadlines.py` and this response document changed for this correction. Native a27268, frozen v1, v2 schema b1689a and contract9ebf89 pins remain those listed above. Parent independently verifies this narrow producer/parser regression and refreshes caller pins; no repeated full combined review or runtime handoff is claimed.


## Pen461 follow-up: actual JSON numeric-token interoperability

The parent found that the initial float-conversion fix was incomplete: Python `float(10**308)` and `float(int(sys.float_info.max))` are finite, but the unchanged Foundation JSON parser rejects those original bare-integer tokens with NSCocoa3840. Those integers were never coerced, so their exact canonical JSON still exercised Foundation's decimal-integer path. The prior139-test result above is historical evidence, not closure of this token mismatch. Parent461 reproducers and requests were read-only and remain unchanged.

The minimum correction adds a public **signed-64-bit positive integer domain (`1..9223372036854775807`)**, alongside positive finite floating-point values. The existing positivity/strict-type/finite checks remain. This deliberately conservative NSNumber-compatible integer subset does not depend on Foundation's much larger decimal range, which the actual parser accepted at `10**165` but rejected at `10**166` in this test. These are measured decimal probes, not a claim about the largest accepted decimal token. No accepted integer is converted to a float or reformatted; ordinary original values, canonical bytes, hashes and legacy recipes remain unchanged. Values above the public integer cap are rejected before preview/execute acquisition even when Foundation happens to accept them.

### Actual unchanged Foundation/ControlledRequest matrix

Every row below was run separately for BOTH deadline fields (26 cases). Accepted-public rows use exact bytes emitted by actual `AppQuartzBackend.run_job`, with wholly inert app/transport objects and zero fake clock origins. For public refusals no backend request can be emitted: the native-only probe replaces only that numeric value in a real producer request and is explicitly marked `exact_producer_bytes:false`. It is not represented as accepted public traffic.

| JSON token/value | Public | Actual native parser |
|---|---|---|
| `420` | accept | accept |
| `420.25` | accept | accept |
| `2**53+1` as bare integer | accept | accept |
| `2**63-1` as bare integer | accept | accept |
| `2**63` as bare integer | reject | accept |
| `2**64-1` as bare integer | reject | accept |
| `10**165` as bare integer | reject | accept |
| `10**166` as bare integer | reject | NSCocoa3840 reject |
| `10**308` as bare integer | reject | NSCocoa3840 reject |
| `int(sys.float_info.max)` as bare integer | reject | NSCocoa3840 reject |
| `10**400` as bare integer | reject | NSCocoa3840 reject |
| `1.7976931348623157e+308` | accept | accept |
| `5e-324` | accept | accept |

The parser harness consists of the original `QuartzPen.swift` definitions before its main call plus a main that only reads JSON and constructs `ControlledRequest`. It is compiled with `SOFTWARE_PEN_INERT_BUILD`; `nm -u` confirms no CGEventPost import. It never calls original main/preparedMain, initializes an application, opens sockets or posts input. Native source hash remains `a27268b4827eee3ed45bf9ad16182d85c5a3b9be0f2329f11cd38dca972d2f0b`. Fork-local ignored evidence is in `local_tests/pen463-domain/{producer-parser-matrix.json,discovery.json,Parser.swift,nm.txt}` with exact per-case request bytes; the final asserted rerun is `local_tests/pen463-domain/final/producer-parser-matrix.json`. `run_deadline_parser_matrix(parser, output)` in the deadline test module reproduces the producer/parser checks with a fresh output directory and an explicitly supplied inert parser; it is never invoked by normal pytest.

Final host validation: **147 passed, one socket-backed test deselected**, comprising the original111 plus36 domain/boundary cases. New public-refusal tests include both reported finite-conversion-but-invalid-integer tokens, integer cap+1 and unsigned64 max; they assert no job/acquire/backend validation or launch. Valid signed64 max, 2**53+1, ordinary numbers and finite float extremes preserve exact types/canonical hashes. The26 native matrix cases additionally pass, as do source compilation, unchanged addon parity and diff whitespace checks. Only `pen.py`, deadline tests and this response changed. Clock/coordinator/custody/native/wire/UNKNOWN policy are unchanged. Parent narrow verification and final caller pin refresh remain; no repeated full combined review or runtime handoff is implied.
