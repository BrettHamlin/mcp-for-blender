# BMCP-1 implementation plan

1. Add opt-in versioned sculpt commands to the existing main-thread dispatcher;
   bind instance/file/object/view/configuration, retain raw derived radius values,
   and restrict captures/checkpoints to a preconfigured artifact directory.
2. Add an external QuartzPen adapter and durable asynchronous coordinator with
   request deduplication, exclusive leases, cancellation, and unknown recovery.
3. Register MCP tools, exercise boundary failures with host fakes, run upstream
   tests, review the diff, publish a draft PR and hand native gates to the parent.

Do not change listener startup or claim a threading crash fix. Do not operate
Blender or native input here. Native runtime acceptance remains pending.

Implementation stages completed offline: guarded addon commands, external CLI
adapter, durable asynchronous MCP coordinator, evidence attachments, host tests,
and wheel verification. See BMCP-1-review.md for findings and
BMCP-1-integration.md for runtime setup and parent-owned native gates.
