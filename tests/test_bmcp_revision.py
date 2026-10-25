"""Independent-review regressions: host fakes only, never native input."""
import asyncio
import copy
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp.server.fastmcp import FastMCP
from blender_mcp import sculpt_tools
from blender_mcp.pen import QuartzBackend, preview
from blender_mcp.pen_jobs import PenJobs
from test_bmcp_pen import recipe, Control, Backend, wait, close
from test_bmcp_control import load_control


def test_old_reconcile_cannot_release_new_store_lease(tmp_path, recipe, monkeypatch):
    c = load_control(SimpleNamespace())(SimpleNamespace(host='localhost'))
    identity = {k: k for k in ('instance', 'file', 'scene_pointer', 'object_pointer',
                              'mesh_pointer', 'window_pointer', 'area_pointer')}
    identity.update(pid=123, mode='SCULPT')
    c.inspect = lambda: dict(identity=identity, binding='a'*64)
    c.sessions['session'] = dict(identity=copy.deepcopy(identity))
    def lost(action, params):
        result = c.dispatch(action, params)
        return {} if action == 'release' else result
    backend = SimpleNamespace(validate=lambda: None, run=lambda *a: dict(
        quiescent=True, release_attempted=True, outcome='completed'))
    monkeypatch.setattr(threading.Thread, 'start', lambda self: None)
    a = PenJobs(tmp_path/'a', backend, lost)
    a.execute('one', recipe, preview(recipe)['recipe_hash'])
    a._run('one', threading.Event())
    assert a.status('one')['lease_held'] and c.lease is None
    old = a.status('one')
    close(a)
    a = PenJobs(tmp_path/'a', backend, c.dispatch)
    backend.run = lambda *a: dict(quiescent=False, outcome='UNKNOWN')
    b = PenJobs(tmp_path/'b', backend, c.dispatch)
    b.execute('one', recipe, preview(recipe)['recipe_hash'])
    b._run('one', threading.Event())
    newer = c.lease
    a.reconcile('one', True, 'External fixture verifies old emitter exit/release')
    assert newer is not None and c.lease == newer
    assert old['acquisition_id'] != b.status('one')['acquisition_id']
    # A duplicate acquire cannot replay a retired acquisition.
    with pytest.raises(ValueError):
        c.dispatch('acquire', dict(session='session', expected_binding='a'*64,
            request_id='one', acquisition_id=old['acquisition_id']))
    close(a)
    close(b)


@pytest.mark.parametrize('release_log', [False, True])
def test_metadata_failure_supervised_through_coordinator(tmp_path, recipe, monkeypatch, release_log):
    import blender_mcp.pen as pen
    calls = []
    class Process:
        pid = 42
        returncode = None
        def poll(self):
            calls.append('poll')
            return self.returncode
        def terminate(self):
            calls.append('terminate')
            self.returncode = 130
            if release_log:
                rows = [dict(post_called=True, kind='contact_end', cg_pressure=0,
                             mouse_pressure=0, buttons=0),
                        dict(post_called=True, kind='proximity_exit', enter=0)]
                (tmp_path/'one'/'emitter.jsonl').write_text('\n'.join(map(json.dumps, rows)))
    monkeypatch.setattr(pen.subprocess, 'Popen', lambda *a, **k: Process())
    original = Path.write_text
    def write(path, *a, **k):
        if path.name == 'process.json':
            raise OSError('injected metadata disk full')
        return original(path, *a, **k)
    monkeypatch.setattr(Path, 'write_text', write)
    backend = QuartzBackend('/inert/fake', 'hash')
    monkeypatch.setattr(backend, 'validate', lambda: None)
    c = Control()
    jobs = PenJobs(tmp_path, backend, c)
    jobs.execute('one', recipe, preview(recipe)['recipe_hash'])
    result = wait(jobs)
    assert 'poll' in calls and calls.count('terminate') == 1
    assert result['state'] == 'UNKNOWN'
    assert result['lease_held'] is not release_log
    assert 'metadata disk full' in result['backend']['error']
    close(jobs)


def make_app(monkeypatch, tmp_path, send):
    monkeypatch.setenv('BMCP_JOB_ROOT', str(tmp_path))
    monkeypatch.setenv('BMCP_PEN_EXECUTABLE', '/inert/fake')
    monkeypatch.setenv('BMCP_PEN_SHA256', 'hash')
    app = FastMCP('review regression')
    sculpt_tools.register(app, lambda: SimpleNamespace(host='localhost', send_command=send))
    return app


@pytest.mark.parametrize('tool,field,base', [
    ('checkpoint_restore', 'discard_current', dict(session='s', expected_binding='a'*64, name='a.blend')),
    ('pen_reconcile', 'native_release_verified', dict(request_id='one', note='external claim'))])
@pytest.mark.parametrize('value', [1, 0, 'yes', 'true', 'false', '', None])
def test_public_safety_bools_reject_coercion(tmp_path, monkeypatch, tool, field, base, value):
    seen = []
    monkeypatch.setattr(sculpt_tools, 'PenJobs', lambda *a: SimpleNamespace(
        reconcile=lambda *a: seen.append(a) or {}))
    app = make_app(monkeypatch, tmp_path, lambda *a, **k: seen.append(a) or {})
    with pytest.raises(Exception):
        asyncio.run(app.call_tool(tool, {**base, field: value}))
    assert seen == []


@pytest.mark.parametrize('value', [True, False])
def test_public_safety_bools_route_literals(tmp_path, monkeypatch, value):
    seen = []
    monkeypatch.setattr(sculpt_tools, 'PenJobs', lambda *a: SimpleNamespace(
        reconcile=lambda rid, verified, note: seen.append(verified) or {}))
    app = make_app(monkeypatch, tmp_path, lambda cmd, args, **k:
                   seen.append(args['params']['discard_current']) or {})
    asyncio.run(app.call_tool('checkpoint_restore', dict(session='s', expected_binding='a'*64,
        name='a.blend', discard_current=value)))
    asyncio.run(app.call_tool('pen_reconcile', dict(request_id='one', native_release_verified=value, note='claim')))
    assert seen == [value, value]


def test_public_blocking_control_does_not_starve_cancel_status(tmp_path, monkeypatch, recipe):
    entered, finish = threading.Event(), threading.Event()
    c = Control()
    backend = Backend(c, hold=True)
    jobs = PenJobs(tmp_path, backend, c)
    jobs.execute('one', recipe, preview(recipe)['recipe_hash'])
    assert backend.started.wait(2)
    monkeypatch.setattr(sculpt_tools, 'PenJobs', lambda *a: jobs)
    def send(*a, **k):
        entered.set()
        finish.wait(.5)
        return {}
    app = make_app(monkeypatch, tmp_path, send)
    async def scenario():
        control = asyncio.create_task(app.call_tool('sculpt_inspect', {}))
        await asyncio.sleep(.03)
        assert entered.is_set()
        assert not control.done(), 'blocking socket call starved event loop'
        await asyncio.wait_for(app.call_tool('pen_status', dict(request_id='one')), .2)
        await asyncio.wait_for(app.call_tool('pen_cancel', dict(request_id='one')), .2)
        assert jobs.cancels['one'].is_set()
        finish.set()
        await control
    try:
        asyncio.run(scenario())
    finally:
        finish.set()
        backend.finish.set()
        wait(jobs)
        close(jobs)


def test_reconcile_socket_wait_does_not_hold_job_lock(tmp_path, monkeypatch, recipe):
    entered, finish = threading.Event(), threading.Event()
    def control(*a):
        entered.set()
        finish.wait(.5)
        return {'released': True}
    jobs = PenJobs(tmp_path, None, control)
    jobs._save(dict(request_id='one', recipe=recipe, state='UNKNOWN', lease_held=True,
                   acquisition_id='11111111-1111-4111-8111-111111111111'))
    monkeypatch.setattr(sculpt_tools, 'PenJobs', lambda *a: jobs)
    app = make_app(monkeypatch, tmp_path, lambda *a, **k: {})
    async def scenario():
        reconcile = asyncio.create_task(app.call_tool('pen_reconcile', dict(request_id='one',
            native_release_verified=True, note='external verification')))
        await asyncio.sleep(.03)
        assert entered.is_set() and not reconcile.done()
        await asyncio.wait_for(app.call_tool('pen_status', dict(request_id='one')), .2)
        await asyncio.wait_for(app.call_tool('pen_cancel', dict(request_id='one')), .2)
        assert not reconcile.done()
        finish.set()
        await reconcile
    try:
        asyncio.run(scenario())
    finally:
        finish.set()
        close(jobs)


def test_legacy_unresolved_job_cannot_guess_acquisition(tmp_path, recipe):
    jobs = PenJobs(tmp_path, None, lambda *a: pytest.fail('Unknown owner must not dispatch'))
    jobs._save(dict(request_id='one', recipe=recipe, state='UNKNOWN', lease_held=True))
    with pytest.raises(ValueError, match='ownership UNKNOWN'):
        jobs.reconcile('one', True, 'verification cannot supply missing owner')
    assert jobs.status('one')['lease_held']
    close(jobs)


@pytest.mark.parametrize('mode', ['timeout', 'signal_error', 'poll_error', 'cancel_race', 'spawn_error'])
def test_adapter_partial_startup_and_bounded_cleanup(tmp_path, recipe, monkeypatch, mode):
    import blender_mcp.pen as pen
    clock = [0]
    calls = []
    cancel = threading.Event()
    monkeypatch.setattr(pen.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(pen.time, 'sleep', lambda delay: clock.__setitem__(0, clock[0]+1))
    class Process:
        pid = 42
        returncode = None
        def poll(self):
            if mode == 'poll_error':
                raise OSError('poll unavailable')
            return self.returncode
        def terminate(self):
            calls.append('terminate')
            if mode == 'signal_error':
                raise OSError('signal unavailable')
            if mode == 'cancel_race':
                self.returncode = 130
    def popen(*a, **k):
        if mode == 'spawn_error':
            raise OSError('spawn failed')
        if mode == 'cancel_race':
            cancel.set()
        return Process()
    monkeypatch.setattr(pen.subprocess, 'Popen', popen)
    backend = QuartzBackend('/inert/fake', 'hash')
    monkeypatch.setattr(backend, 'validate', lambda: None)
    if mode == 'spawn_error':
        with pytest.raises(OSError, match='spawn failed'):
            backend.run(recipe, tmp_path, cancel)
        assert not calls
    else:
        result = backend.run(recipe, tmp_path, cancel)
        assert calls == ['terminate']
        assert clock[0] <= 27
        assert result['release'] == 'UNKNOWN'
        assert result['quiescent'] is (mode == 'cancel_race')
