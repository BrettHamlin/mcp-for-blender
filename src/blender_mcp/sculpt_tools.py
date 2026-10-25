"""Small opt-in control API layered on the existing local Blender connection."""
from __future__ import annotations

import os
import threading

from .pen import QuartzBackend, Recipe, preview
from .pen_jobs import PenJobs


def register(mcp, connection):
    coordinator = None
    coordinator_lock = threading.Lock()

    def control(action, params):
        blender = connection()
        if blender.host not in ('localhost', '127.0.0.1', '::1'):
            raise ValueError('BMCP control requires a loopback Blender connection')
        return blender.send_command('bmcp_control', {'action': action, 'params': params},
                                    read_only=action == 'inspect')

    def jobs():
        nonlocal coordinator
        with coordinator_lock:
            if coordinator is None:
                root = os.environ.get('BMCP_JOB_ROOT')
                executable = os.environ.get('BMCP_PEN_EXECUTABLE')
                sha = os.environ.get('BMCP_PEN_SHA256')
                if not root or not os.path.isabs(root) or not executable or not sha:
                    raise ValueError('Configure BMCP_JOB_ROOT, BMCP_PEN_EXECUTABLE and BMCP_PEN_SHA256 explicitly')
                coordinator = PenJobs(root, QuartzBackend(executable, sha), control)
            return coordinator

    @mcp.tool()
    def sculpt_inspect() -> dict:
        """Read BMCP v1 exact reported state; live sculpt mesh synchronization remains unverified."""
        return control('inspect', {})

    @mcp.tool()
    def sculpt_bind(expected_binding: str) -> dict:
        """Bind the inspected instance/file/object/view; requires Blender BMCP_ARTIFACT_ROOT."""
        return control('bind', {'expected_binding': expected_binding})

    @mcp.tool()
    def sculpt_configure(session: str, expected_binding: str, settings: dict) -> dict:
        """Set brush size/strength/pressure flags with strict state guard; returns readback."""
        return control('configure', {'session': session, 'expected_binding': expected_binding, 'settings': settings})

    @mcp.tool()
    def sculpt_capture(session: str, expected_binding: str, name: str) -> dict:
        """Capture a new viewport PNG with before/after state and method; not a full OS window."""
        return control('capture', {'session': session, 'expected_binding': expected_binding, 'name': name})

    @mcp.tool()
    def checkpoint_save(session: str, expected_binding: str, name: str) -> dict:
        """Save a new .blend copy in this session's directory; never overwrite."""
        return control('save', {'session': session, 'expected_binding': expected_binding, 'name': name})

    @mcp.tool()
    def checkpoint_restore(session: str, expected_binding: str, name: str, discard_current: bool = False) -> dict:
        """Reopen a session-owned hashed checkpoint, explicitly discarding current bound scene. Invalidates sessions."""
        return control('restore', {'session': session, 'expected_binding': expected_binding,
                                   'name': name, 'discard_current': discard_current})

    @mcp.tool()
    def pen_preview(recipe: Recipe) -> dict:
        """Validate one sampled contact without input; returns canonical recipe/hash. No native preflight."""
        return preview(recipe.model_dump(mode='json', by_alias=True))

    @mcp.tool()
    def pen_execute(request_id: str, recipe: Recipe, expected_recipe_hash: str) -> dict:
        """Dispatch one hash-bound contact asynchronously. Same ID/payload returns existing job, never replays."""
        return jobs().execute(request_id, recipe.model_dump(mode='json', by_alias=True), expected_recipe_hash)

    @mcp.tool()
    def pen_status(request_id: str) -> dict:
        """Read persisted job and separate evidence layers. Missing receiver/effect evidence is UNKNOWN."""
        return jobs().status(request_id)

    @mcp.tool()
    def pen_cancel(request_id: str) -> dict:
        """Request SIGTERM cleanup from a live emitter; return promptly. Native release is not guaranteed."""
        return jobs().cancel(request_id)

    @mcp.tool()
    def pen_reconcile(request_id: str, native_release_verified: bool, note: str) -> dict:
        """Release an unresolved lease only after external verification of emitter exit and native release; never replays."""
        return jobs().reconcile(request_id, native_release_verified, note)

    @mcp.tool()
    def pen_record_evidence(request_id: str, layer: str, verdict: str, artifact: str,
                            sha256: str, reviewer: str) -> dict:
        """Attach an external received/applied/form review with a hash-verified job-local artifact. Does not promote machine UNKNOWN."""
        return jobs().record_evidence(request_id, layer, verdict, artifact, sha256, reviewer)
