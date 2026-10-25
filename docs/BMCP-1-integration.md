# BMCP-1 integration packet

**Offline implementation only. Do not install into the active Blender profile or
start a listener until the parent authorizes a disposable native validation.**
No Blender process, GUI, OS input, permission grant, or native emitter was used
for implementation tests. The original live worker retains native ownership.

## Pairing and compatibility

Use the Python package and `addon.py` from the **same BMCP-1-impl commit**. The
bundled addon is byte-identical to the root addon. Upstream package 2.2.4,
addon 1.8 and protocol 15 remain unchanged; the optional extension identifies
its state/session/recipe/job contracts as `bmcp-*/v1`. Do not use the upstream
version handshake alone to identify this fork; pin the commit and addon hash.
No change to listener defaults, autostart, asset tools, or raw execution policy.
The default MCP tool list remains upstream's. `BMCP_ENABLE_CONTROL=1` adds the
extension tools. A stock addon will reject `bmcp_control`, without fallback to
arbitrary Python execution.

## Isolated setup (prepare only until parent authorizes runtime)

From the prepared fork checkout:

```sh
UV_CACHE_DIR=/tmp/bmcp-uv-cache uv venv .venv
UV_CACHE_DIR=/tmp/bmcp-uv-cache uv pip install --python .venv/bin/python -e . pytest
.venv/bin/python -m pytest tests/test_bmcp_control.py tests/test_bmcp_pen.py tests/test_bmcp_tools.py -q
```

For a future isolated MCP process, explicitly set:

- `BMCP_ENABLE_CONTROL=1`
- `BMCP_JOB_ROOT`: absolute, caller-owned durable directory, unique to the test
  instance; retain it across coordinator restarts. Never erase it to retry an ID.
- `BMCP_PEN_EXECUTABLE`: absolute path to the **existing approved** QuartzPen
  executable inside its configured app identity.
- `BMCP_PEN_SHA256`: independently verified hash of that executable.
- Existing `BLENDER_HOST=127.0.0.1` and a parent-approved dedicated port.

For the future disposable Blender process, set `BMCP_ARTIFACT_ROOT` to an existing
absolute caller-owned directory. Install this commit's addon only in its isolated
profile. Do not enable/start it until the parent approves. Each bound session
creates a private subdirectory. Capture/save accept basenames only and refuse
existing artifacts. Restore only opens a session-recorded checkpoint whose bytes
still match, with explicit `discard_current=true` and the current state binding.
Both safety attestations (`discard_current` and `native_release_verified`) accept
only literal JSON booleans; numbers, strings and null are rejected at MCP dispatch.
Restore invalidates sessions; inspect and bind again. This is not Dyntopo rollback.

## State and capture contract

`sculpt_inspect` reads one active mesh and exactly one VIEW_3D, without mode
switches, forced redraw, or mesh synchronization. `sculpt_bind(expected_binding)`
requires a saved file and binds a random session to the instance, file, scene,
object, mesh, window and area identities. State binding includes object/view/
projection matrices, raw Blender window/region geometry, mode, reported brush/
unified/sculpt settings and an RNA mesh vertex/polygon hash. View movement,
configuration changes, geometry readback changes or a different instance refuse
stale requests. The tool exposes the precise fields it covers: this is **not an
exhaustive Blender state fingerprint** (textures, modifiers, masks, arbitrary
brush curves, overlays and every RNA field are not serialized). It is not a
continuous native target monitor. User edits or window movement after the
acquire readback remain a runtime risk; the approved disposable test must retain
exclusive UI custody for the contact.

Raw brush/unified `unprojected_radius` is preserved separately. In VIEW size mode
it is excluded from strict comparison as a derived readback; in SCENE size mode
it is additionally included as configured input. Geometry is explicitly labeled
`RNA mesh; live sculpt synchronization unverified`; no applied verdict follows
from an unchanged or changed hash. `sculpt_configure` writes only raw brush size,
strength and pressure flags, after full input validation. Unified overrides are
reported and are not silently disabled.

`sculpt_capture` reuses upstream capture, records before/after states, timestamp,
image hash and method (`offscreen` or `window_grab`). It is viewport coverage,
not a full OS-window capture. A changed strict binding returns
`strict_state_preserved=false`; it does not hide the image or derived values.
GPU/capture errors propagate. No transport rewrite or crash-cause claim is made.

## Pen protocol and provenance

`pen_preview` accepts `bmcp-pen/v1`: session, expected state binding, exact PID,
explicit `[x,y,width,height]` safety rectangle and one
`software-pen-path-samples/v1` stroke. It returns the normalized recipe and hash.
MCP exposes the nested strict schema. Coordinates are literally
`quartz-global: origin main-display top-left, x right, y down`, in native Quartz
points. No Retina scaling or coordinate conversion is inferred. The caller must
calibrate the intended viewport rectangle against the disposable window; the
coordinator cannot prove the screen mapping from a viewport PNG. One contact,
2–1201 samples, 0.01–10 seconds, <=240 Hz, pressure [0,1], finite coordinates and
exclusive upper/right bounds preserve the existing native limits. Curved and
pressure-varying paths are passed unchanged; use an existing authoring tool to
sample them. No new curve/event generator was copied into the fork.

The legacy direct-executable adapter invokes this CLI without a shell:

```
QuartzPen --input <job>/stroke.json --bounds X Y WIDTH HEIGHT --emit --target-pid PID
```

Contract inspected in the user-owned software-pen `QuartzPen.swift` source,
SHA256 `ec375a80322304cb92d72f681a653be8f939213da90d92c598a57a6dd0bef7ac`
(on 2026-10-25). Source origin: LSL1 `tools/software-pen/QuartzPen.swift`, including
its Options, validate, checkTarget, run and receipt functions; no source code or
experiment fixtures are vendored/imported from that checkout. The approved
binary hash is separately supplied, not assumed to equal the source hash.
That executor requires macOS and Blender **4.1.1**, exact foreground PID, existing
post-event permission, and active display bounds. It performs hover, endpoint
movement, release and proximity exit, and attempts cleanup on SIGTERM. This fork
does not rebuild/re-sign/install it, request permission or focus Blender. Retain
the existing app identity. Other executor versions require explicit contract
review, not merely updating the hash. Upstream MIT attribution remains intact.

## Jobs, cancellation and evidence

`pen_execute(request_id, recipe, expected_recipe_hash)` durably records the job
before acquiring the addon lease. The record includes a random UUID
`acquisition_id`, generated once and committed before dispatch. Request IDs are
store-local; acquisition UUIDs distinguish owners across stores and restarts.
Acquire/release require the exact `(session, request_id, acquisition_id)` tuple.
The addon retains released tuples for its instance lifetime: a duplicate release
acknowledges the old release without touching a newer owner, an active duplicate
acquire acknowledges the same owner, and a retired acquire refuses replay.
Unknown owners refuse release even when no lease is active. Addon restart loses
sessions and retired tuples and therefore refuses old-session recovery. Legacy
job records without acquisition identity remain UNKNOWN; reconciliation cannot
invent an identity for them. Pair package/addon from this revision because the
lease wire parameters have changed within this draft contract.

All extension MCP handlers are async and offload blocking host calls. The existing
connection lock still serializes socket requests and Blender dispatch still uses
its main-thread queue; no bpy operation moves to a host thread. Reconciliation
waits for the socket outside the coordinator lock, so status/cancel remain
available during a slow control command. Cancelling an MCP await does not abort
an already dispatched socket operation or imply native release.

Execution returns promptly; a host worker obtains
and finishes the short acquire command **before** starting the external process.
Blender's main thread never waits for the pen. The addon rejects other MCP
commands while leased (inspect, ping and owned release remain available).
The coordinator allows one unresolved operation; another coordinator cannot
open the same store. Multiple stores still contend on the addon's global lease.
These are cooperative local guards, not an authentication boundary. Upstream raw
socket/code interfaces remain powerful; `safe_mode` does not secure the socket.

Same ID + same normalized payload returns the persisted job, including after
restart. Conflicting payload refuses. Pending/running jobs become UNKNOWN on
restart and retain exclusivity. Timeout/exception never auto-replays. A lost
acquire response also requires reconciliation even if nothing was posted.
Native input cannot be atomically bound to a Blender state hash; there is a
small acquire-to-process-start interval and no native file/object check per event.

`pen_cancel` requests cancellation. The legacy direct adapter sends SIGTERM to its own process
handle and waits for cleanup; after 20 seconds it also requests SIGTERM, allowing
five seconds to exit. It never SIGKILLs, signals a recovered PID, or injects an
extra contact. If the process does not exit, or logs do not show both zero-pressure
up and proximity exit, the lease stays unresolved. A process exit alone is not
release evidence. Process custody begins immediately after Popen: failure to
persist `process.json` triggers bounded SIGTERM cleanup and polling rather than
escaping supervision. Such jobs retain an UNKNOWN outcome, even if exit plus
emitter up/exit observations allow lease release. Poll/termination errors are
also bounded; unverified exit retains the lease. After the cleanup deadline the
worker returns UNKNOWN without further signalling; external verification is
required. Log output goes to files, not pipes, so cleanup does not wait for pipe
readers or an output-flush operation. Cancel after a lost process handle reports UNKNOWN.

`pen_status` returns durable request/recipe/hash/state, emitter result and separate
posted/received/applied/form_achieved/release fields. Complete or partial emitter
records may establish only observed post calls; coverage remains unverified.
Receiver, geometry effect and artistic form remain UNKNOWN. Fake tests never
supply native acceptance. `pen_record_evidence` attaches a hash-verified job-local
artifact and external review verdict without promoting machine UNKNOWN. Save
original receiver logs/images and provenance in the job directory; external
review records are append-only and exact duplicate attachments are idempotent.

`pen_reconcile` requires an explicit external verification that the emitter has
exited and native contact is released, plus a provenance note. It releases the
matching addon lease but leaves historical evidence UNKNOWN. If Blender has
restarted or file/object identity changed, reconciliation refuses the old
session: parent must verify cleanup and start a new scoped validation session.
Checkpoint manifests are session-memory state, not a cross-restart recovery
catalog. Durable job records survive; native recovery acceptance is pending.

## Parent-owned disposable native gates

1. Verify exact commit/package/addon pair and isolated directories. Confirm no
   other worker owns the disposable instance, UI or native input.
2. With approved scoped listener, inspect saved plane/sphere, bind, compare raw
   values/matrices to originals. Reject wrong instance/file/view and moved window.
3. Capture current/alternate views, inspect original images, compare strict state
   and raw derived radius independently. Repeat the known crash-sensitive boundary
   only under the parent's approved diagnostic. Do not claim a crash fix.
4. Save copy, make a deliberate disposable change, restore with explicit discard,
   re-inspect/rebind and compare reopened state. Exercise capture/save errors,
   missing/modified checkpoint, disconnect and restart recovery.
5. Calibrate Quartz frame and safe viewport bounds. Preview one tiny contact,
   verify recipe/hash/settings/PID, then execute once. Retain full emitter and
   actual receiver observations, geometry and original before/after images.
6. Demonstrate release/cancel, same-ID retrieval, conflicting ID refusal, concurrent
   writer refusal, missing receipt UNKNOWN and restart without replay. Verify
   emitter exit and native release before reconciling any unresolved lease.
7. Close only the disposable process/listener, verify port/process cleanup, preserve
   artifacts/job DB. No active-profile install, global configuration or original
   worker hold changes. Parent owns visible acceptance and any merge decision.

A useful initial PR can be source-reviewed and host-tested with all seven native
gates still pending. Do not merge as native-validated based on these host tests.

## App custody adapter (source-only preparation)

`BMCP_PEN_APP` selects the new `AppQuartzBackend`; it never falls back to the
legacy direct-Popen route if application custody fails. Pin the canonical existing
`.app` path, its `Contents/MacOS` executable and executable SHA256. The legacy
route described above remains available for compatibility, but does not repair
MCP424's observed launch-route access mismatch. No wrapper or file-reported PID
stands in for the application object.

The optional `pen-app` dependency supplies PyObjC Cocoa (lock resolves 12.2.2).
It was absent from the implementation environment; no Cocoa package was installed
or native app launched for these tests. The source bridge uses
[NSWorkspace's completion callback](https://developer.apple.com/documentation/appkit/nsworkspace/openapplication(at:configuration:completionhandler:))
with `createsNewApplicationInstance=true`, retaining its actual application even
when completion is late or metadata persistence fails. Cocoa calls run on the
MCP host's main asyncio loop, with a nonblocking Cocoa main-run-loop turn and a
bounded worker wait. This is the host process, not Blender's main thread.
[NSRunningApplication properties](https://developer.apple.com/documentation/appkit/nsrunningapplication)
require those run-loop turns to refresh; background polling alone is insufficient.
The bridge has only fake-framework coverage. Runtime callback delivery, property
refresh and platform identity behavior remain unverified.

The controller implements software-pen-custody/v1 from original427's stable
synthesis, read-only provenance SHA256
`64bfaf2c551225861b3b0d4587d98a4afbf4910d8c199889f92ba91bdcba1e85`.
Original426 established native API compile/link evidence only. No original source
or emitter binary is copied, rebuilt, signed, installed or replaced here.

A private owned 0700 job directory contains exclusive 0600 stroke/request files;
the socket is 0600 in a separate short canonical 0700 directory under
`/private/tmp`. Endpoint and accepted descriptors are close-on-exec. Exact request
bytes, stroke bytes, opaque recipe hash, durable acquisition UUID and fresh
challenge bind all frames. One strict UTF-8 LF-delimited JSON frame is at most
8192 bytes. Duplicate keys, unknown READY keys, wrong types, stale binding,
out-of-order data and mismatched identities refuse authorization.

READY must bind the controller's own TASK_AUDIT_TOKEN and the peer's kernel
UID/PID/LOCAL_PEERTOKEN to the actual retained application, an equal fresh lookup,
a live state and the expected executable bytes. The checks repeat before
AUTHORIZE. LOCAL_PEERTOKEN is dynamic last_pid attribution, **not** an immutable
connection token or pidfd. Descriptor delegation and reconnect are unsupported.
The coordinator commits `authorization_intent` to SQLite before dispatch. Lost
response or cancellation never retries an authorization; existing request-ID
retrieval/conflict rules and acquisition lease ownership remain intact.

Default app mode passes only `--prepare-session REQUEST`; it sends CANCEL after
an authenticated READY and cannot request posting. The separate explicit setting
`BMCP_PEN_ALLOW_AUTHORIZED_EMISSION=1` adds `--allow-authorized-emission`. This is
configuration for a future reviewed live gate, not current execution authority.
There is no focus side effect; original425's existing exact-PID FocusBlender
operation remains the native owner's separate responsibility.

Cancellation uses the authenticated channel, then closes the endpoint. EOF,
launch callback error, timeout, or launcher state never proves exit. Only the
retained application's terminated state can do so. A bounded job response keeps
UNKNOWN and the addon lease when evidence is incomplete; a retained ticket and
read-only supervisor survive uncertain/late application exit. There is no PID
signal, SIGKILL, or replacement cleanup contact. Cancelling an MCP await does not
cancel the already dispatched host worker.

The final parser follows the frozen machine-readable schema
`software-pen-custody-v1.schema.json`, SHA256
`1a24c0a2257b0a18f24a361f10ae513127c95229e7aa6ddbee5cf55a2bde7b0b`.
READY has exactly 12 keys; FINISHED has exactly 18. The outcome spellings are
`completed`, `cancelled`, `unknown`; booleans do not accept numbers or strings.
Completed requires authorization consumed, complete evidence, sender release
observed and null error. Cancelled requires this controller to have sent CANCEL.
Unknown/incomplete metadata retains the lease even if the app later exits.

Receipt accounting was read from original428 source SHA256
`1e5b6a97b9dca20f6f4f055907c3564e8d4fb4c62160e598c8734dde1c79b6f4`.
The exact receipt path must match the request, be an owned regular 0600 file
without a symlink or additional hard link, and match its raw-byte SHA256. The
native buffer cap is 4,000,000 bytes / 1211 records, plus one final accounting
line. The resumed native implementation now caps the complete file, including
that footer, at 4,000,000 bytes; the controller enforces the same bound.
Current source checked read-only: `cd07a654353bcc433a0013e47d74468a0ea6213a8d7981f7ee1b95e36d272f6c`. Its footer has exactly
`log_status`, `evidence_complete`, `buffered_records`, `dropped_records`, `outcome`,
`sender_release_observed`, `post_calls_observed`. Counts and release observations
are recomputed from retained records. A complete receipt has a final LF, matching
counts, no dropped records and consistent outcome. Partial bytes may be retained
and hashed, but cannot establish complete release.

A valid final frame still cannot establish exit: the controller waits for the
retained application object, rejects additional frames, and sends no final ACK.
A complete cancelled PREPARE-only session with no consumed authorization or
sender posts may release its lease after actual app termination. Authorized work
also requires returned zero-pressure contact_end and proximity_exit observations.
These are sender facts, never native release or effect proof.

Parent-owned combined independent review and native validation remain pending.
The installed approved app is unchanged and does not acquire this protocol merely
because the adapter source exists. Native receiver delivery, native release,
geometry effect and art remain UNKNOWN; prior observation420/checkpoint423 results
and whole-art FAIL are not re-tested or changed by host mocks.


## CONCRETE429 launch diagnostic delta

The combined direct review cleared base `584b6d98f1f32c05923510a234287700b6dda2a2`
for bounded preparation (15/15 files); that verdict does not cover this delta.
The original owner's 429 invocation returned UNKNOWN / quiescent true after
11.6655 seconds; normal host return was 11.6677 seconds. Its retained application
was terminated at profile time 11.7120 seconds, whose origin precedes the backend
elapsed-time origin. Do not subtract those different origins as a callback delay.
The invocation, result, trace, receipt, request, stroke, host-finished record,
host log and invocation source were checked against retained actual-SHA256SUMS.

Source trace: the direct host runs asyncio on the main thread and run_job in
asyncio.to_thread. own_token precedes launch; it consumes part of the existing
preparation budget. on_main dispatches launch to that asyncio loop and turns
NSRunLoop before NSWorkspace submission. The callback stores the actual app and
sets LaunchTicket.completed. The worker refreshes Cocoa while awaiting that event,
then admits the connection and reads READY. A callback can complete during a
refresh, including at the deadline; the existing cancellation/deadline check
still runs before the next loop condition. No admission ordering is changed here.
On rejection, endpoint closure precedes retained-app exit observation. The raw
receipt's controller-disconnected error is consistent with this path, but does
not establish the native connection's precise arrival time. It proves neither
callback starvation nor a kernel-peer failure.

No cause of 429 is established. The profile omitted callback entry and launch/
refresh boundaries; absence of a callback profile row is not absence of callback
execution. Only its eventual retained application is established. Fake Cocoa
and main-asyncio tests can reproduce late completion or stalled dispatch by
controlling them explicitly, but cannot attribute either mechanism to 429.

The single proposed next diagnostic is to enable the existing adapter's new
`blender_mcp.pen_app.launch` DEBUG logger in the original owner's existing inert
invocation. Use a fast host-owned log sink, preserve its records through late
custody cleanup, and restore its logging configuration afterward. No new runner,
launch mode, wire field or runtime attempt is supplied or authorized by this delta.
Each record has a monotonic timestamp, Python thread ID and main-thread flag;
ticket and dispatch numbers are process-local object IDs, not process identities.
No arguments, paths, audit tokens, challenge, app repr or protocol payload is logged.

Read the first distinguishing observation against `launch_requested.prepare_deadline`:

- `main_submit` / `main_dispatch` / `runloop_enter` / `runloop_return` distinguish
  scheduling delay from time inside refresh; `workspace_enter` / `workspace_return`
  bound submission. `main_wait_timeout.cancelled` says whether unstarted dispatch
  was suppressed; `main_dispatch.admitted=false` confirms it never executed.
- `callback_enter` and `ticket_completed` distinguish callback entry from ticket
  publication. Compare their timestamps to the fixed deadline and
  `ticket_wait_rejected` (completed/cancelled snapshots) or `ticket_wait_return`.
  This observes Python callback entry, not when Cocoa first scheduled the block.
- `connection_accepted` and `ready_read_enter/return` expose subsequent admission
  without reading or logging credential bytes. Acceptance is not authentication;
  all existing identity and final-evidence checks still apply.

Logging is optional and sink exceptions do not discard callback custody. Logging
can perturb timing; a blocked sink or missing records cannot establish native
causality. No timeout increase, automatic retry, replay, fallback or relaxed
identity check is included. Frozen schema `1a24c0…b0b` and native source
`cd07a654…272f6c` are unchanged. Parent decides delta review and original owns any
later inert native validation. Native delivery, release, effect and art gates
remain unchanged; 429 remains UNKNOWN.
