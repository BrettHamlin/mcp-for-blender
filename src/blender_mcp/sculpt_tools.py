"""Small opt-in control API layered on the existing local Blender connection."""
from __future__ import annotations

import asyncio
import os
import threading

from pydantic import StrictBool

from .pen import QuartzBackend, Recipe, preview
from .pen_jobs import PenJobs
from .pen_custody import AppQuartzBackend


def register(mcp, connection):
    coordinator = None
    coordinator_lock = threading.Lock()

    def control(action, params):
        blender = connection()
        if blender.host not in ('localhost', '127.0.0.1', '::1'):
            raise ValueError('BMCP control requires a loopback Blender connection')
        return blender.send_command('bmcp_control', {'action': action, 'params': params},
                                    read_only=action == 'inspect')

    def jobs(main_loop=None):
        nonlocal coordinator
        with coordinator_lock:
            if coordinator is None:
                root = os.environ.get('BMCP_JOB_ROOT')
                executable = os.environ.get('BMCP_PEN_EXECUTABLE')
                sha = os.environ.get('BMCP_PEN_SHA256')
                if not root or not os.path.isabs(root) or not executable or not sha:
                    raise ValueError('Configure BMCP_JOB_ROOT, BMCP_PEN_EXECUTABLE and BMCP_PEN_SHA256 explicitly')
                app = os.environ.get('BMCP_PEN_APP')
                if app:
                    backend = AppQuartzBackend(app, executable, sha,
                        allow_emission=os.environ.get('BMCP_PEN_ALLOW_AUTHORIZED_EMISSION') == '1',
                        main_loop=main_loop)
                else:
                    backend = QuartzBackend(executable, sha)
                coordinator = PenJobs(root, backend, control)
            return coordinator

    @mcp.tool()
    async def sculpt_inspect() -> dict:
        """Read BMCP v1 exact reported state; live sculpt mesh synchronization remains unverified."""
        return await asyncio.to_thread(control, 'inspect', {})

    @mcp.tool()
    async def sculpt_bind(expected_binding: str) -> dict:
        """Bind the inspected instance/file/object/view; requires Blender BMCP_ARTIFACT_ROOT."""
        return await asyncio.to_thread(control, 'bind', {'expected_binding': expected_binding})

    @mcp.tool()
    async def sculpt_configure(session: str, expected_binding: str, settings: dict) -> dict:
        """Set brush size/strength/pressure flags with strict state guard; returns readback."""
        return await asyncio.to_thread(control, 'configure', {'session': session, 'expected_binding': expected_binding, 'settings': settings})

    @mcp.tool()
    async def sculpt_capture(session: str, expected_binding: str, name: str) -> dict:
        """Capture a new viewport PNG with before/after state and method; not a full OS window."""
        return await asyncio.to_thread(control, 'capture', {'session': session, 'expected_binding': expected_binding, 'name': name})

    @mcp.tool()
    async def checkpoint_save(session: str, expected_binding: str, name: str) -> dict:
        """Save a new .blend copy in this session's directory; never overwrite."""
        return await asyncio.to_thread(control, 'save', {'session': session, 'expected_binding': expected_binding, 'name': name})

    @mcp.tool()
    async def checkpoint_restore(session: str, expected_binding: str, name: str, discard_current: StrictBool = False) -> dict:
        """Reopen a session-owned hashed checkpoint, explicitly discarding current bound scene. Invalidates sessions."""
        return await asyncio.to_thread(control, 'restore', {'session': session, 'expected_binding': expected_binding,
                                   'name': name, 'discard_current': discard_current})

    @mcp.tool()
    async def pen_preview(recipe: Recipe) -> dict:
        """Validate one sampled contact without input; returns canonical recipe/hash. No native preflight."""
        return await asyncio.to_thread(preview, recipe.model_dump(mode='json', by_alias=True))

    @mcp.tool()
    async def pen_execute(request_id: str, recipe: Recipe, expected_recipe_hash: str) -> dict:
        """Dispatch one hash-bound contact asynchronously. Same ID/payload returns existing job, never replays."""
        main_loop = asyncio.get_running_loop()
        return await asyncio.to_thread(lambda: jobs(main_loop).execute(request_id, recipe.model_dump(mode='json', by_alias=True), expected_recipe_hash))

    @mcp.tool()
    async def pen_status(request_id: str) -> dict:
        """Read persisted job and separate evidence layers. Missing receiver/effect evidence is UNKNOWN."""
        main_loop = asyncio.get_running_loop()
        return await asyncio.to_thread(lambda: jobs(main_loop).status(request_id))

    @mcp.tool()
    async def pen_cancel(request_id: str) -> dict:
        """Request owned emitter cleanup; return promptly. Native release is not guaranteed."""
        main_loop = asyncio.get_running_loop()
        return await asyncio.to_thread(lambda: jobs(main_loop).cancel(request_id))

    @mcp.tool()
    async def pen_reconcile(request_id: str, native_release_verified: StrictBool, note: str) -> dict:
        """Release an unresolved lease only after external verification of emitter exit and native release; never replays."""
        main_loop = asyncio.get_running_loop()
        return await asyncio.to_thread(lambda: jobs(main_loop).reconcile(request_id, native_release_verified, note))

    @mcp.tool()
    async def pen_record_evidence(request_id: str, layer: str, verdict: str, artifact: str,
                            sha256: str, reviewer: str) -> dict:
        """Attach an external received/applied/form review with a hash-verified job-local artifact. Does not promote machine UNKNOWN."""
        main_loop = asyncio.get_running_loop()
        return await asyncio.to_thread(lambda: jobs(main_loop).record_evidence(request_id, layer, verdict, artifact, sha256, reviewer))
