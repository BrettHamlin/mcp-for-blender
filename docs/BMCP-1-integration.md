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
It invalidates sessions; inspect and bind again. This is not Dyntopo rollback.

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

The real adapter invokes this stable CLI directly, without a shell:

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
before acquiring the addon lease. It returns promptly; a host worker obtains
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

`pen_cancel` requests cancellation. The adapter sends SIGTERM to its own process
handle and waits for cleanup; after 20 seconds it also requests SIGTERM, allowing
five seconds to exit. It never SIGKILLs, signals a recovered PID, or injects an
extra contact. If the process does not exit, or logs do not show both zero-pressure
up and proximity exit, the lease stays unresolved. A process exit alone is not
release evidence. Cancel after a lost process handle reports UNKNOWN.

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
