"""Inert host controller tests. All application objects and peer identities are fakes."""
import asyncio
import json
import os
from pathlib import Path
import socket
import threading
import time
import uuid
from types import SimpleNamespace

import pytest

from blender_mcp import pen_custody as custody
from blender_mcp.pen import canonical, preview
from blender_mcp.pen_app import DarwinApplications, LaunchTicket
from blender_mcp.pen_jobs import PenJobs
from test_bmcp_pen import recipe, Control, wait, close


def finished_fixture(binding, path, *, authorized=True, cancelled=False):
    rows = [dict(post_called=True, kind='contact_end', cg_pressure=0, mouse_pressure=0, buttons=0),
            dict(post_called=True, kind='proximity_exit', enter=0)] if authorized else []
    outcome = 'cancelled' if cancelled else 'completed'
    status = dict(log_status='complete', evidence_complete=True, buffered_records=len(rows),
                  dropped_records=0, outcome=outcome, sender_release_observed=authorized,
                  post_calls_observed=len(rows))
    data = b''.join((canonical(row)+'\n').encode() for row in rows+[status])
    custody.persist(Path(path), data)
    return dict(binding, kind='finished', emitter_pid=42, emitter_audit_token='b'*64,
                outcome=outcome, authorized=authorized, receipt_path=str(path), receipt_sha256=custody.digest(data),
                evidence_complete=True, post_calls_observed=len(rows), sender_release_observed=authorized,
                native_release='UNKNOWN', error=None)


class Applications:
    def __init__(self, executable, scenario='normal'):
        self.path = executable
        self.scenario = scenario
        self.app = SimpleNamespace(dead=False)
        self.calls = []
        self.commands = []
        self.peers = 0
        self.lookup_count = 0
        self.worker = None
        self.request = None
        self.ready_sent = threading.Event()
        self.complete_launch = threading.Event()

    def refresh(self):
        pass

    def own_token(self):
        return 'a' * 64

    def pid(self, app):
        assert app is self.app
        return 42

    def executable(self, app):
        return self.path

    def terminated(self, app):
        assert app is self.app
        return app.dead

    def fresh(self, pid):
        assert pid == 42
        self.lookup_count += 1
        if self.scenario == 'stale' or self.scenario == 'dynamic_gap' and self.lookup_count > 1:
            return object()
        return self.app

    def same(self, app, fresh):
        return app is fresh

    def peer(self, conn):
        assert not conn.get_inheritable()
        self.peers += 1
        if self.scenario == 'changed_executable' and self.peers == 1:
            self.path.write_bytes(b'changed')
        if self.scenario == 'peer_changes' and self.peers == 2:
            return os.getuid(), 99, 'b' * 64
        return os.getuid(), (99 if self.scenario == 'wrong_peer' else 42), 'b' * 64

    def launch(self, ticket, app_path, args):
        self.calls.append(args)
        request_bytes = Path(args[1]).read_bytes()
        request = self.request = json.loads(request_bytes)
        assert len(request) == 14
        assert request['stroke_sha256'] == custody.digest(Path(request['stroke_path']).read_bytes())
        assert Path(request['socket_path']).stat().st_mode & 0o777 == 0o600
        assert Path(args[1]).stat().st_mode & 0o777 == 0o600
        binding = {k: request[k] for k in custody.COMMON - {'request_sha256'}}
        binding['request_sha256'] = custody.digest(request_bytes)
        self.binding = binding
        if self.scenario != 'late_completion':
            ticket.complete(self.app, None)

        def peer():
            try:
                if self.scenario == 'late_completion':
                    self.complete_launch.wait(1)
                    ticket.complete(self.app, None)
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
                    conn.settimeout(.5)
                    conn.connect(request['socket_path'])
                    if self.scenario == 'no_ready':
                        time.sleep(.1)
                        return
                    ready = dict(binding, kind='ready', emitter_pid=42, emitter_audit_token='b'*64,
                                 controller_peer_audit_token='a'*64,
                                 emission_enabled='--allow-authorized-emission' in args, posted=False)
                    if self.scenario == 'bad_ready':
                        ready['posted'] = 0
                    if self.scenario == 'wrong_binding':
                        ready['request_sha256'] = 'f'*64
                    encoded = (canonical(ready)+'\n').encode()
                    conn.sendall(encoded * (2 if self.scenario == 'pipelined_ready' else 1))
                    self.ready_sent.set()
                    command = conn.makefile('rb').readline()
                    if command:
                        self.commands.append(json.loads(command))
                    if self.scenario in ('lost_ack', 'disconnect'):
                        return
                    if self.scenario == 'timeout':
                        time.sleep(.2)
                    if self.scenario.startswith('finished') or self.scenario in ('prepare_finished', 'cancel_finished'):
                        cancelled = self.commands[-1]['kind'] == 'cancel'
                        authorized = self.commands[-1]['kind'] == 'authorize'
                        if self.scenario == 'cancel_finished':
                            command = conn.makefile('rb').readline()
                            self.commands.append(json.loads(command))
                            cancelled = True
                        frame = finished_fixture(binding, request['receipt_path'], authorized=authorized, cancelled=cancelled)
                        if self.scenario == 'finished_bad_hash':
                            frame['receipt_sha256'] = 'f'*64
                        wire = (canonical(frame)+'\n').encode()
                        conn.sendall(wire * (2 if self.scenario == 'finished_duplicate' else 1))
            except (OSError, ValueError):
                pass
            finally:
                if self.scenario not in ('uncertain_exit', 'finished_uncertain_exit'):
                    self.app.dead = True
        self.worker = threading.Thread(target=peer)
        self.worker.start()


@pytest.fixture
def setup(tmp_path, recipe):
    executable = tmp_path / 'Emitter.app' / 'Contents' / 'MacOS' / 'Emitter'
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b'inert fixture, not an executable program')
    executable.chmod(0o700)
    job = dict(acquisition_id=str(uuid.uuid4()), recipe=preview(recipe)['recipe'],
               recipe_hash=preview(recipe)['recipe_hash'])
    directory = tmp_path / 'job'
    directory.mkdir(mode=0o700)
    instances = []
    def make(scenario='normal', enabled=True):
        apps = Applications(executable, scenario)
        backend = custody.AppQuartzBackend(executable.parent.parent.parent, executable,
            custody.digest(executable.read_bytes()), allow_emission=enabled, applications=apps)
        backend.prepare_seconds = .08
        backend.session_seconds = .15
        backend.cleanup_seconds = .15
        instances.append((apps, backend))
        return backend, apps, job, directory
    yield make
    for apps, backend in instances:
        apps.complete_launch.set()
        if apps.worker:
            apps.worker.join(1)
        apps.app.dead = True


@pytest.mark.parametrize('scenario', ['wrong_peer', 'stale', 'dynamic_gap', 'changed_executable',
    'peer_changes', 'no_ready', 'bad_ready', 'wrong_binding', 'pipelined_ready'])
def test_identity_and_readiness_never_authorize(setup, scenario):
    backend, apps, job, directory = setup(scenario)
    intents = []
    result = backend.run_job(job, directory, threading.Event(), intents.append)
    assert result['outcome'] == 'UNKNOWN'
    assert not any(c['kind'] == 'authorize' for c in apps.commands)
    assert not result['release_attempted']
    if scenario not in ('changed_executable', 'peer_changes'):
        assert not intents


@pytest.mark.parametrize('scenario', ['lost_ack', 'disconnect', 'timeout', 'uncertain_exit'])
def test_dispatch_never_replays_and_keeps_unknown(setup, scenario):
    backend, apps, job, directory = setup(scenario)
    intents = []
    result = backend.run_job(job, directory, threading.Event(), intents.append)
    apps.worker.join(1)
    assert len(intents) == 1
    assert [c['kind'] for c in apps.commands] == ['authorize']
    assert result['outcome'] == 'UNKNOWN' and not result['release_attempted']
    assert result['quiescent'] is (scenario != 'uncertain_exit')
    if scenario == 'uncertain_exit':
        assert backend.owners[job['acquisition_id']].application is apps.app
    with pytest.raises((FileExistsError, ValueError)):
        backend.run_job(job, directory, threading.Event(), intents.append)
    assert len(apps.calls) == 1


def test_prepare_only_cancels_without_intent(setup):
    backend, apps, job, directory = setup(enabled=False)
    intents = []
    result = backend.run_job(job, directory, threading.Event(), intents.append)
    assert '--allow-authorized-emission' not in apps.calls[0]
    assert [c['kind'] for c in apps.commands] == ['cancel']
    assert not intents and result['outcome'] == 'UNKNOWN'


@pytest.mark.parametrize('failure', ['application.json', 'intent'])
def test_metadata_failure_retains_custody_and_cancels(setup, monkeypatch, failure):
    backend, apps, job, directory = setup()
    original = custody.persist
    def persist(path, data):
        if path.name == failure:
            raise OSError('inert disk full')
        original(path, data)
    monkeypatch.setattr(custody, 'persist', persist)
    def intent(binding):
        raise OSError('inert intent disk full')
    result = backend.run_job(job, directory, threading.Event(), intent)
    assert 'disk full' in result['error']
    assert result['quiescent'] and not result['release_attempted']
    assert not any(c['kind'] == 'authorize' for c in apps.commands)


def test_cancel_during_durable_intent_never_dispatches(setup):
    backend, apps, job, directory = setup()
    cancel = threading.Event()
    result = backend.run_job(job, directory, cancel, lambda binding: cancel.set())
    assert not any(c['kind'] == 'authorize' for c in apps.commands)
    assert result['outcome'] == 'UNKNOWN'


def test_late_launch_callback_keeps_application_owned(setup):
    backend, apps, job, directory = setup('late_completion')
    backend.cleanup_seconds = .01
    result = backend.run_job(job, directory, threading.Event(), lambda binding: None)
    assert not result['quiescent']
    ticket = backend.owners[job['acquisition_id']]
    apps.complete_launch.set()
    apps.worker.join(1)
    assert ticket.application is apps.app
    assert result['outcome'] == 'UNKNOWN'


@pytest.mark.parametrize('data', [b'[]', b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":1e999}', b'{"a":"\xff"}'])
def test_strict_json(data):
    with pytest.raises((ValueError, UnicodeError)):
        custody.strict_object(data)


@pytest.mark.parametrize('payload', [b'x'*8192, b'{}\n', b'\xff\n', b'[]\n'])
def test_frame_rejection(payload):
    a, b = socket.socketpair()
    try:
        channel = custody.Channel(a, {'schema': custody.SCHEMA}, time.monotonic()+.1, threading.Event())
        b.sendall(payload)
        with pytest.raises((ValueError, UnicodeError)):
            channel.read()
    finally:
        a.close()
        b.close()


def test_path_guards_and_exclusive_persistence(tmp_path):
    private = tmp_path / 'private'
    private.mkdir(mode=0o700)
    link = tmp_path / 'link'
    link.symlink_to(private, target_is_directory=True)
    with pytest.raises(ValueError):
        custody.private_directory(link)
    private.chmod(0o755)
    with pytest.raises(ValueError):
        custody.private_directory(private)
    custody.persist(private/'record', b'one')
    with pytest.raises(FileExistsError):
        custody.persist(private/'record', b'two')
    assert (private/'record').read_bytes() == b'one'


def test_actual_coordinator_commits_intent_retains_lease_and_dedupes(setup, tmp_path, recipe):
    backend, apps, _, _ = setup('lost_ack')
    control = Control()
    jobs = PenJobs(tmp_path/'store', backend, control)
    original = jobs._save
    def save(job):
        original(job)
        if job.get('authorization_intent'):
            assert not any(c['kind'] == 'authorize' for c in apps.commands) or job['state'] == 'UNKNOWN'
    jobs._save = save
    try:
        jobs.execute('one', recipe, preview(recipe)['recipe_hash'])
        result = wait(jobs)
        assert result['authorization_intent'] == apps.binding
        assert result['lease_held'] and result['state'] == 'UNKNOWN'
        assert jobs.execute('one', recipe, preview(recipe)['recipe_hash']) == result
        assert len(apps.calls) == 1
    finally:
        close(jobs)
    reopened = PenJobs(tmp_path/'store', backend, control)
    try:
        assert reopened.execute('one', recipe, preview(recipe)['recipe_hash'])['lease_held']
        assert len(apps.calls) == 1
    finally:
        close(reopened)


def test_launch_bridge_retains_callback_application_and_requests_new_instance():
    calls = []
    config = SimpleNamespace(**{name: (lambda value, name=name: calls.append((name, value))) for name in
        ('setCreatesNewApplicationInstance_', 'setActivates_', 'setAddsToRecentItems_', 'setArguments_')})
    app = object()
    def launch(url, configuration, callback):
        assert configuration is config
        callback(app, None)
    bridge = object.__new__(DarwinApplications)
    bridge.appkit = SimpleNamespace(NSWorkspaceOpenConfiguration=SimpleNamespace(configuration=lambda: config),
        NSWorkspace=SimpleNamespace(sharedWorkspace=lambda: SimpleNamespace(
            openApplicationAtURL_configuration_completionHandler_=launch)))
    bridge.foundation = SimpleNamespace(NSURL=SimpleNamespace(fileURLWithPath_=lambda path: path),
        NSRunLoop=SimpleNamespace(mainRunLoop=lambda: SimpleNamespace(
            runMode_beforeDate_=lambda *args: calls.append(('runloop', True)))),
        NSDefaultRunLoopMode='default', NSDate=SimpleNamespace(date=lambda: 0))
    ticket = LaunchTicket()
    bridge.launch(ticket, '/inert.app', ['--prepare-session', '/inert.json'])
    assert ticket.application is app and ticket.completed.is_set()
    assert ('setCreatesNewApplicationInstance_', True) in calls
    assert ('setActivates_', False) in calls


def test_cocoa_queries_turn_host_main_runloop_from_worker():
    seen = []
    bridge = object.__new__(DarwinApplications)
    app = SimpleNamespace(dead=False, isTerminated=lambda: app.dead)
    def turn(*args):
        assert threading.current_thread() is threading.main_thread()
        seen.append('refresh')
        app.dead = True
    bridge.foundation = SimpleNamespace(
        NSRunLoop=SimpleNamespace(mainRunLoop=lambda: SimpleNamespace(runMode_beforeDate_=turn)),
        NSDefaultRunLoopMode='default', NSDate=SimpleNamespace(date=lambda: 0))
    async def scenario():
        bridge.main_loop = asyncio.get_running_loop()
        assert await asyncio.to_thread(bridge.terminated, app)
    asyncio.run(scenario())
    assert seen == ['refresh']


def test_host_loop_stall_cancels_unstarted_launch_operation():
    queued = []
    bridge = object.__new__(DarwinApplications)
    bridge.main_loop = SimpleNamespace(call_soon_threadsafe=queued.append)
    ran = []
    errors = []
    def worker():
        try:
            bridge.on_main(lambda: ran.append(True))
        except RuntimeError as exc:
            errors.append(str(exc))
    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(2)
    assert not thread.is_alive() and 'UNKNOWN' in errors[0]
    queued[0]()
    assert not ran


def test_endpoint_cleanup_error_still_observes_retained_application(setup, monkeypatch):
    backend, apps, job, directory = setup('lost_ack')
    original = Path.rmdir
    def fail_once(path):
        original(path)
        if path.name.startswith('bmcp-'):
            raise OSError('inert cleanup error')
    monkeypatch.setattr(Path, 'rmdir', fail_once)
    result = backend.run_job(job, directory, threading.Event(), lambda binding: None)
    assert result['quiescent'] and result['outcome'] == 'UNKNOWN'
    assert not backend.owners


def test_cancelled_mcp_await_preserves_dispatched_app_custody(setup, tmp_path, recipe, monkeypatch):
    from blender_mcp import sculpt_tools
    from mcp.server.fastmcp import FastMCP
    backend, apps, _, _ = setup('uncertain_exit')
    jobs = PenJobs(tmp_path/'async-store', backend, Control())
    entered, release = threading.Event(), threading.Event()
    execute = jobs.execute
    def dispatched(*args):
        value = execute(*args)
        entered.set()
        release.wait(1)
        return value
    jobs.execute = dispatched
    monkeypatch.setenv('BMCP_JOB_ROOT', str(tmp_path/'async-store'))
    monkeypatch.setenv('BMCP_PEN_EXECUTABLE', str(apps.path))
    monkeypatch.setenv('BMCP_PEN_SHA256', backend.sha256)
    monkeypatch.setattr(sculpt_tools, 'PenJobs', lambda *args: jobs)
    mcp = FastMCP('inert-custody')
    sculpt_tools.register(mcp, lambda: None)
    async def scenario():
        call = asyncio.create_task(mcp.call_tool('pen_execute', dict(request_id='one',
            recipe=recipe, expected_recipe_hash=preview(recipe)['recipe_hash'])))
        assert await asyncio.to_thread(entered.wait, 1)
        assert await asyncio.to_thread(apps.ready_sent.wait, 1)
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call
        await asyncio.wait_for(mcp.call_tool('pen_status', dict(request_id='one')), .2)
        await asyncio.wait_for(mcp.call_tool('pen_cancel', dict(request_id='one')), .2)
        release.set()
        result = await asyncio.to_thread(wait, jobs)
        assert result['lease_held'] and result['state'] == 'UNKNOWN'
        if apps.calls:
            assert backend.owners[result['acquisition_id']].application is apps.app
    try:
        asyncio.run(scenario())
    finally:
        release.set()
        apps.app.dead = True
        wait(jobs)
        close(jobs)


@pytest.mark.parametrize('scenario,expected', [('finished', 'completed'), ('prepare_finished', 'cancelled'),
    ('finished_bad_hash', 'UNKNOWN'), ('finished_duplicate', 'UNKNOWN'), ('finished_uncertain_exit', 'UNKNOWN')])
def test_finished_requires_receipt_accounting_and_actual_exit(setup, scenario, expected):
    backend, apps, job, directory = setup(scenario, enabled=scenario != 'prepare_finished')
    result = backend.run_job(job, directory, threading.Event(), lambda binding: None)
    assert result['outcome'] == expected
    assert result['release_attempted'] is (expected != 'UNKNOWN')
    assert 'native release UNKNOWN' in result['release'] or result['release'] in ('UNKNOWN', 'not authorized; no sender posts reported')
    if scenario == 'finished_uncertain_exit':
        assert not result['quiescent'] and job['acquisition_id'] in backend.owners


def test_matching_cancel_after_authorize_receives_cleanup_summary(setup):
    backend, apps, job, directory = setup('cancel_finished')
    cancel = threading.Event()
    def trigger():
        assert apps.ready_sent.wait(1)
        while not apps.commands:
            time.sleep(.001)
        cancel.set()
    thread = threading.Thread(target=trigger)
    thread.start()
    result = backend.run_job(job, directory, cancel, lambda binding: None)
    thread.join(1)
    assert [c['kind'] for c in apps.commands] == ['authorize', 'cancel']
    assert result['outcome'] == 'cancelled' and result['release_attempted']
    assert result['finished']['native_release'] == 'UNKNOWN'


@pytest.fixture
def final_case(tmp_path):
    directory = tmp_path/'receipt'
    directory.mkdir(mode=0o700)
    binding = dict(schema=custody.SCHEMA, acquisition_id=str(uuid.uuid4()), challenge='a'*64,
                   request_sha256='b'*64, recipe_sha256='c'*64, stroke_sha256='d'*64)
    path = directory/'receipt.jsonl'
    frame = finished_fixture(binding, path)
    ready = dict(emitter_pid=42, emitter_audit_token='b'*64)
    def verify(value):
        return custody.verify_finished(value, binding, ready, path, True, False)
    return frame, path, verify


@pytest.mark.parametrize('patch', [
    {'extra': 1}, {'kind': 'ready'}, {'outcome': 'UNKNOWN'}, {'native_release': 'PASS'},
    {'authorized': 1}, {'evidence_complete': 1}, {'sender_release_observed': 1},
    {'post_calls_observed': True}, {'post_calls_observed': -1}, {'emitter_pid': True},
    {'emitter_pid': 99}, {'emitter_audit_token': 'f'*64}, {'recipe_sha256': 'f'*64},
    {'receipt_sha256': None}, {'receipt_path': None, 'receipt_sha256': None},
    {'receipt_path': '/unowned/receipt'}, {'receipt_sha256': 'F'*64}, {'error': ''},
    {'error': 'metadata failure'}, {'authorized': False}, {'sender_release_observed': False},
    {'evidence_complete': False}, {'outcome': 'cancelled'},
])
def test_finished_exact_types_bindings_and_cross_field_rules(final_case, patch):
    frame, path, verify = final_case
    frame.update(patch)
    with pytest.raises(ValueError):
        verify(frame)


@pytest.mark.parametrize('mutation', ['hash', 'count', 'dropped', 'release', 'missing_status', 'extra_status',
                                     'bool_pressure', 'mode', 'symlink', 'partial_line', 'oversized'])
def test_receipt_verification_refuses_mismatch(final_case, mutation):
    frame, path, verify = final_case
    rows = [json.loads(line) for line in path.read_bytes().splitlines()]
    if mutation == 'count': rows[-1]['buffered_records'] += 1
    if mutation == 'dropped': rows[-1]['dropped_records'] = 1
    if mutation == 'release': rows[0]['buttons'] = 1
    if mutation == 'missing_status': rows.pop()
    if mutation == 'extra_status': rows.append(rows[-1])
    if mutation == 'bool_pressure': rows[0]['cg_pressure'] = False
    data = b''.join((canonical(row)+'\n').encode() for row in rows)
    if mutation == 'partial_line': data = data[:-1]
    path.write_bytes(data)
    if mutation == 'oversized':
        with path.open('r+b') as stream:
            stream.truncate(custody.RECEIPT_LIMIT + 1)
    if mutation == 'mode': path.chmod(0o644)
    if mutation == 'symlink':
        target = path.with_name('target')
        path.rename(target)
        path.symlink_to(target)
    if mutation != 'hash': frame['receipt_sha256'] = custody.digest(data)
    else: frame['receipt_sha256'] = 'f'*64
    with pytest.raises((ValueError, OSError)):
        verify(frame)


def test_partial_metadata_remains_unknown(final_case):
    frame, path, verify = final_case
    frame.update(outcome='unknown', evidence_complete=False, error='metadata unavailable',
                 receipt_path=None, receipt_sha256=None)
    verify(frame)


def test_complete_job_releases_only_matching_coordinator_lease(setup, tmp_path, recipe):
    backend, apps, _, _ = setup('finished')
    control = Control()
    jobs = PenJobs(tmp_path/'complete-store', backend, control)
    try:
        jobs.execute('one', recipe, preview(recipe)['recipe_hash'])
        result = wait(jobs)
        assert result['state'] == 'completed' and not result['lease_held']
        assert control.calls == ['acquire', 'release']
        assert all(result['evidence'][key] == 'UNKNOWN' for key in ('received', 'applied', 'form_achieved'))
        assert 'native release UNKNOWN' in result['evidence']['release']
        assert jobs.execute('one', recipe, preview(recipe)['recipe_hash']) == result
        assert len(apps.calls) == 1
    finally:
        close(jobs)
