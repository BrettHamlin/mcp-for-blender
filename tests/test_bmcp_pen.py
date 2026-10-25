import copy
import json
import threading
from pathlib import Path

import pytest

from blender_mcp.pen import FRAME, QuartzBackend, emitter_evidence, preview
from blender_mcp.pen_jobs import PenJobs


@pytest.fixture
def recipe():
    return {'schema': 'bmcp-pen/v1', 'session': 'session', 'expected_binding': 'a'*64,
            'target_pid': 123, 'bounds': [0, 0, 200, 200],
            'stroke': {'schema': 'software-pen-path-samples/v1', 'scope': 'HOST_ONLY',
                'coordinates': {'frame': FRAME, 'units': 'points'}, 'time_units': 'seconds',
                'pressure_range': [0, 1], 'duration': 1,
                'samples': [{'event': 'contact_begin', 'x': 10, 'y': 20, 'time': 0, 'pressure': .2},
                            {'event': 'contact_end', 'x': 30, 'y': 40, 'time': 1, 'pressure': .8}]}}


class Control:
    def __init__(self):
        self.lease = None
        self.in_command = False
        self.calls = []
        self.pid = 123

    def __call__(self, action, params):
        self.in_command = True
        try:
            self.calls.append(action)
            if action == 'acquire':
                assert self.lease is None
                self.lease = params['request_id']
                return {'acquired': True, 'state': {'identity': {'pid': self.pid}}}
            assert self.lease == params['request_id']
            self.lease = None
            return {'released': True, 'state': {}}
        finally:
            self.in_command = False


class Backend:
    def __init__(self, control, hold=False):
        self.control = control
        self.calls = 0
        self.started = threading.Event()
        self.finish = threading.Event()
        if not hold:
            self.finish.set()

    def validate(self):
        pass

    def run(self, recipe, directory, cancel):
        assert not self.control.in_command, 'Blender command must return before pen stream'
        assert self.control.lease is not None
        self.calls += 1
        self.started.set()
        assert self.finish.wait(3)
        return {'quiescent': True, 'release_attempted': True, 'outcome': 'cancelled' if cancel.is_set() else 'completed'}


def wait(jobs, request='one'):
    jobs.threads[request].join(4)
    assert not jobs.threads[request].is_alive()
    return jobs.status(request)


def close(jobs):
    jobs.db.close()
    jobs.owner.close()


def test_idempotency_concurrency_cancel_and_missing_receipts(tmp_path, recipe):
    control = Control()
    backend = Backend(control, hold=True)
    jobs = PenJobs(tmp_path, backend, control)
    digest = preview(recipe)['recipe_hash']
    jobs.execute('one', recipe, digest)
    assert backend.started.wait(2)
    assert jobs.execute('one', recipe, digest)['request_id'] == 'one'
    with pytest.raises(ValueError, match='owns'):
        jobs.execute('two', recipe, digest)
    different = copy.deepcopy(recipe)
    different['stroke']['samples'][0]['pressure'] = .3
    with pytest.raises(ValueError, match='conflicts'):
        jobs.execute('one', different, preview(different)['recipe_hash'])
    jobs.cancel('one')
    backend.finish.set()
    result = wait(jobs)
    assert result['state'] == 'cancelled'
    assert result['evidence']['received'] == result['evidence']['applied'] == 'UNKNOWN'
    assert result['evidence']['release'] == 'UNKNOWN'
    assert backend.calls == 1
    assert control.lease is None
    jobs.execute('one', recipe, digest)
    assert backend.calls == 1
    close(jobs)


def test_restart_does_not_replay_or_drop_unknown_lease(tmp_path, recipe):
    control = Control()
    backend = Backend(control)
    jobs = PenJobs(tmp_path, backend, control)
    job = {'request_id': 'one', 'recipe': recipe, 'recipe_hash': preview(recipe)['recipe_hash'],
           'state': 'running', 'lease_held': True, 'evidence': {}}
    jobs._save(job)
    close(jobs)
    jobs = PenJobs(tmp_path, backend, control)
    assert jobs.status('one')['state'] == 'UNKNOWN'
    jobs.execute('one', recipe, job['recipe_hash'])
    assert backend.calls == 0
    with pytest.raises(ValueError, match='owns'):
        jobs.execute('two', recipe, job['recipe_hash'])
    assert 'UNKNOWN' in jobs.cancel('one')['cancel_note']
    close(jobs)


def test_two_coordinators_cannot_share_store(tmp_path):
    jobs = PenJobs(tmp_path, None, None)
    with pytest.raises(RuntimeError, match='Another coordinator'):
        PenJobs(tmp_path, None, None)
    close(jobs)


def test_wrong_target_never_invokes_backend(tmp_path, recipe):
    control = Control()
    control.pid = 999
    backend = Backend(control)
    jobs = PenJobs(tmp_path, backend, control)
    jobs.execute('one', recipe, preview(recipe)['recipe_hash'])
    assert wait(jobs)['state'] == 'refused'
    assert backend.calls == 0
    assert control.lease is None
    close(jobs)


def test_lost_acquire_response_is_unknown_never_replayed(tmp_path, recipe):
    def lost(action, params):
        raise TimeoutError('response lost')
    backend = Backend(Control())
    jobs = PenJobs(tmp_path, backend, lost)
    digest = preview(recipe)['recipe_hash']
    jobs.execute('one', recipe, digest)
    assert wait(jobs)['state'] == 'UNKNOWN'
    assert jobs.status('one')['lease_held']
    jobs.execute('one', recipe, digest)
    assert backend.calls == 0
    close(jobs)


@pytest.mark.parametrize('mutation', [
    lambda r: r.update(unrecognized=True),
    lambda r: r.update(target_pid=True),
    lambda r: r['stroke']['coordinates'].update(frame='pixels'),
    lambda r: r['stroke']['samples'][0].update(x=200),
    lambda r: r['stroke']['samples'][0].update(pressure=float('nan')),
    lambda r: r['stroke']['samples'][1].update(time=0),
    lambda r: r['stroke']['samples'][0].update(event='contact_end'),
    lambda r: r['stroke'].update(duration=11),
    lambda r: r.update(bounds=[0, 0, -1, 2]),
])
def test_malformed_recipe_refused(recipe, mutation):
    mutation(recipe)
    with pytest.raises(ValueError):
        preview(recipe)


def test_absent_and_partial_log_never_claim_native_pass(tmp_path, recipe):
    path = tmp_path / 'log'
    assert emitter_evidence(path, recipe)['posted'] == 'UNKNOWN'
    path.write_text('{"post_called":true}\n{"log_status":"complete"}\n')
    evidence = emitter_evidence(path, recipe)
    assert evidence['posted'] == 'observed; coverage unverified'
    for key in ('received', 'applied', 'form_achieved', 'release'):
        assert evidence[key] == 'UNKNOWN'
    path.write_text('{"post_called":true}\ntruncated')
    assert emitter_evidence(path, recipe)['posted'] == 'UNKNOWN'


def test_real_adapter_argv_and_cancel_cleanup_request(tmp_path, recipe, monkeypatch):
    import blender_mcp.pen as pen
    calls = []
    class Process:
        pid = 42
        returncode = None
        def poll(self):
            return self.returncode
        def terminate(self):
            calls.append('SIGTERM')
            self.returncode = 130
    def popen(argv, **kwargs):
        calls.append(argv)
        assert kwargs['start_new_session'] is True
        assert '--emit' in argv
        assert json.loads(Path(argv[2]).read_text()) == recipe['stroke']
        return Process()
    backend = QuartzBackend('/configured/QuartzPen', 'hash')
    monkeypatch.setattr(backend, 'validate', lambda: None)
    monkeypatch.setattr(pen.subprocess, 'Popen', popen)
    cancel = threading.Event()
    # Cancellation is requested immediately after launch, without any native input.
    original = popen
    def cancelling(*args, **kwargs):
        result = original(*args, **kwargs)
        cancel.set()
        return result
    monkeypatch.setattr(pen.subprocess, 'Popen', cancelling)
    result = backend.run(recipe, tmp_path, cancel)
    assert calls[0][-2:] == ['--target-pid', '123']
    assert calls[1] == 'SIGTERM'
    assert result['outcome'] == 'cancelled'
    assert result['received'] == result['release'] == 'UNKNOWN'


def test_partial_execution_retains_lease_until_explicit_reconciliation(tmp_path, recipe):
    control = Control()
    backend = Backend(control)
    backend.run = lambda *args: {'quiescent': True, 'outcome': 'completed', 'release': 'UNKNOWN'}
    jobs = PenJobs(tmp_path, backend, control)
    jobs.execute('one', recipe, preview(recipe)['recipe_hash'])
    result = wait(jobs)
    assert result['state'] == 'UNKNOWN' and result['lease_held']
    assert control.lease == 'one'
    assert 'No live process handle' in jobs.cancel('one')['cancel_note']
    with pytest.raises(ValueError, match='verification'):
        jobs.reconcile('one', False, 'not verified')
    resolved = jobs.reconcile('one', True, 'External test fixture confirms process exit and native release')
    assert resolved['state'] == 'UNKNOWN'  # Reconciliation never retroactively makes evidence pass.
    assert resolved['lease_held'] is False
    assert control.lease is None
    close(jobs)


def test_emitter_release_requires_up_and_exit(tmp_path, recipe):
    path = tmp_path / 'log'
    rows = [{'kind': 'contact_end', 'post_called': True, 'cg_pressure': 0,
             'mouse_pressure': 0, 'buttons': 0}]
    path.write_text('\n'.join(map(json.dumps, rows)))
    assert not emitter_evidence(path, recipe)['release_attempted']
    rows.append({'kind': 'proximity_exit', 'post_called': True, 'enter': 0})
    path.write_text('\n'.join(map(json.dumps, rows)))
    evidence = emitter_evidence(path, recipe)
    assert evidence['release_attempted'] is True
    assert evidence['received'] == 'UNKNOWN'


def test_recipe_hash_and_platform_fail_before_dispatch(tmp_path, recipe, monkeypatch):
    import blender_mcp.pen as pen
    backend = QuartzBackend('/not/configured', 'x')
    monkeypatch.setattr(pen.sys, 'platform', 'linux')
    jobs = PenJobs(tmp_path, backend, lambda *a: pytest.fail('must not contact Blender'))
    with pytest.raises(ValueError, match='Recipe changed'):
        jobs.execute('one', recipe, '0'*64)
    with pytest.raises(RuntimeError, match='macOS'):
        jobs.execute('one', recipe, preview(recipe)['recipe_hash'])
    close(jobs)


def test_external_evidence_is_hash_bound_and_does_not_promote_machine_evidence(tmp_path, recipe):
    import hashlib
    control = Control()
    jobs = PenJobs(tmp_path, Backend(control), control)
    jobs.execute('one', recipe, preview(recipe)['recipe_hash'])
    wait(jobs)
    artifact = tmp_path / 'one' / 'review.json'
    artifact.write_text('{"review":"fixture"}')
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='hash mismatch'):
        jobs.record_evidence('one', 'applied', 'PASS', 'review.json', '0'*64, 'fixture')
    with pytest.raises(ValueError, match='basename'):
        jobs.record_evidence('one', 'applied', 'PASS', '../review.json', digest, 'fixture')
    result = jobs.record_evidence('one', 'applied', 'PASS', 'review.json', digest, 'fixture')
    assert result['external_reviews'][0]['verdict'] == 'PASS'
    assert result['evidence']['applied'] == 'UNKNOWN'
    result = jobs.record_evidence('one', 'applied', 'PASS', 'review.json', digest, 'fixture')
    assert len(result['external_reviews']) == 1
    close(jobs)


def test_mcp_execute_invokes_real_coordinator_and_injected_backend(tmp_path, recipe, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from mcp.server.fastmcp import FastMCP
    from blender_mcp import sculpt_tools
    control = Control()
    backend = Backend(control, hold=True)
    monkeypatch.setattr(sculpt_tools, 'QuartzBackend', lambda *args: backend)
    monkeypatch.setenv('BMCP_JOB_ROOT', str(tmp_path))
    monkeypatch.setenv('BMCP_PEN_EXECUTABLE', '/configured/existing')
    monkeypatch.setenv('BMCP_PEN_SHA256', 'configured')
    def send(command, params, **kwargs):
        assert command == 'bmcp_control'
        return control(params['action'], params['params'])
    app = FastMCP('test')
    sculpt_tools.register(app, lambda: SimpleNamespace(host='localhost', send_command=send))
    args = {'request_id': 'mcp-one', 'recipe': recipe, 'expected_recipe_hash': preview(recipe)['recipe_hash']}
    asyncio.run(app.call_tool('pen_execute', args))
    assert backend.started.wait(2)
    asyncio.run(app.call_tool('pen_execute', args))
    assert backend.calls == 1
    asyncio.run(app.call_tool('pen_cancel', {'request_id': 'mcp-one'}))
    backend.finish.set()
    # Wait on an observable host outcome, not any native UI.
    import time
    deadline = time.monotonic() + 3
    while control.lease is not None and time.monotonic() < deadline:
        time.sleep(.01)
    assert control.lease is None
    assert control.calls == ['acquire', 'release']


def test_lost_release_acknowledgement_keeps_unknown_lease(tmp_path, recipe):
    control = Control()
    backend = Backend(control)
    def lost_release(action, params):
        result = control(action, params)
        return result if action == 'acquire' else {}
    jobs = PenJobs(tmp_path, backend, lost_release)
    jobs.execute('one', recipe, preview(recipe)['recipe_hash'])
    result = wait(jobs)
    assert result['lease_held'] and result['state'] == 'UNKNOWN'
    close(jobs)
