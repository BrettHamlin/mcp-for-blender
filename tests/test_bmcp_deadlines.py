"""451/454 regressions using actual methods and wholly inert transport/app objects."""
import copy
import json
import sys
import threading
from types import SimpleNamespace as NS
from pathlib import Path

import pytest

from blender_mcp import pen_custody as custody, pen_deadlines as deadlines
from blender_mcp.pen import EmissionDeadlines, QuartzBackend, Recipe, canonical, preview, recipe_hash
from blender_mcp.pen_jobs import PenJobs
from test_bmcp_pen import recipe, Control, close, wait
from test_bmcp_custody import Applications, finished_fixture


@pytest.fixture
def clock(monkeypatch):
    clock = NS(mach=10., unix=1000.)
    timer = NS(monotonic=lambda: clock.mach, time=lambda: clock.unix,
               get_clock_info=lambda name: NS(implementation='mach_absolute_time()', monotonic=True, adjustable=False))
    monkeypatch.setattr(deadlines, 'time', timer)
    monkeypatch.setattr(custody, 'time', timer)
    return clock


@pytest.fixture
def bound(recipe):
    recipe['emission_deadlines'] = dict(fixture_posting_cutoff_mach_seconds=420., assignment_expires_unix=1100.)
    return recipe


@pytest.mark.parametrize('field', ['fixture_posting_cutoff_mach_seconds', 'assignment_expires_unix'])
@pytest.mark.parametrize('value', [True, False, None, '420', 0, -1, float('inf'), float('nan')])
def test_deadline_numbers_strict(bound, field, value):
    bound['emission_deadlines'][field] = value
    with pytest.raises(ValueError):
        preview(bound)


def test_immutable_normalization_legacy_hash_and_public_schema(recipe, bound):
    values = EmissionDeadlines.model_validate(bound['emission_deadlines'])
    with pytest.raises(ValueError):
        values.assignment_expires_unix = 1200
    legacy = copy.deepcopy(recipe)
    legacy.pop('emission_deadlines')
    assert preview(legacy)['recipe'] == legacy
    assert preview(legacy)['recipe_hash'] == recipe_hash(legacy)
    assert preview(bound)['recipe_hash'] != preview(legacy)['recipe_hash']
    from blender_mcp.pen import Recipe
    assert 'emission_deadlines' in Recipe.model_json_schema()['properties']


def test_live_clock_proof_and_terminal_wall_expiry(bound, clock, monkeypatch):
    gate = deadlines.EmissionAuthority(bound)
    gate.check()
    clock.unix = 1100
    with pytest.raises(deadlines.DeadlineExpired, match='original assignment expired'):
        gate.check()
    clock.unix = 900
    with pytest.raises(deadlines.DeadlineExpired):
        gate.check()
    monkeypatch.setattr(deadlines.time, 'get_clock_info', lambda _: NS(implementation='clock_gettime(CLOCK_MONOTONIC)', monotonic=True, adjustable=False))
    with pytest.raises(RuntimeError, match='not verified'):
        deadlines.EmissionAuthority(bound).check()


class InertTransport:
    def __init__(self, directory, clock, monkeypatch, *, ready_delay=2, intent_delay=0, dead=True, finish_expired=False):
        self.clock, self.ready_delay = clock, ready_delay
        self.commands, self.intents, self.exits = [], [], []
        self.incoming = b''
        self.reads = 0
        self.app = NS(dead=False)
        self.directory = directory
        self.dead = dead
        self.intent_delay = intent_delay
        self.finish_expired = finish_expired
        self.retained = []
        monkeypatch.setattr(custody, 'socket', NS(socket=lambda *a: self, AF_UNIX=1, SOCK_STREAM=1))
        monkeypatch.setattr(custody, 'select', NS(select=lambda r,w,e,t: (r,w,e)))
        monkeypatch.setattr(custody.tempfile, 'mkdtemp', lambda **kw: str(self.make_socket_dir()))
        self.apps = Applications(Path('/inert/App.app/Contents/MacOS/Pen'))
        self.apps.app = self.app
        self.apps.launch = self.launch
        self.backend = custody.AppQuartzBackend('/inert/App.app', str(self.apps.path), 'inert', applications=self.apps, allow_emission=True)
        monkeypatch.setattr(self.backend, 'validate', lambda: None)
        monkeypatch.setattr(self.backend, 'check_executable', lambda: None)
        monkeypatch.setattr(self.backend, '_observe_exit', self.observe)
        monkeypatch.setattr(self.backend, '_retain_until_exit', lambda *args: self.retained.append(args))

    def make_socket_dir(self):
        path = self.directory / 'endpoint'
        path.mkdir(mode=0o700)
        return path

    def set_inheritable(self, value): pass
    def get_inheritable(self): return False
    def bind(self, path): Path(path).touch()
    def listen(self, count): pass
    def setblocking(self, value): pass
    def accept(self): return self, None
    def close(self): pass

    def launch(self, ticket, app_path, arguments):
        data = Path(arguments[1]).read_bytes()
        self.request = json.loads(data)
        self.binding = {k: self.request[k] for k in custody.COMMON - {'request_sha256'}}
        self.binding['request_sha256'] = custody.digest(data)
        ticket.complete(self.app, None)
        ready = dict(self.binding, kind='ready', emitter_pid=42, emitter_audit_token='b'*64,
                     controller_peer_audit_token='a'*64, emission_enabled=True, posted=False)
        self.incoming = (canonical(ready)+'\n').encode()

    def recv(self, count):
        self.reads += 1
        if self.reads == 1:
            self.clock.mach += self.ready_delay
            self.clock.unix += self.ready_delay
        data, self.incoming = self.incoming[:count], self.incoming[count:]
        return data

    def send(self, data):
        frame = json.loads(bytes(data))
        self.commands.append(frame['kind'])
        if frame['kind'] == 'authorize':
            if self.finish_expired:
                self.clock.mach = 421
            frame = finished_fixture(self.binding, Path(self.request['receipt_path']))
            if self.finish_expired:
                # Rewrite a consistent complete unknown receipt for native expiry after cleanup.
                path = Path(frame['receipt_path'])
                rows = [json.loads(line) for line in path.read_text().splitlines()]
                rows[-1]['outcome'] = 'unknown'
                encoded = b''.join((canonical(row)+'\n').encode() for row in rows)
                path.write_bytes(encoded)
                frame.update(outcome='unknown', error='fixture posting cutoff expired', receipt_sha256=custody.digest(encoded))
            self.incoming = (canonical(frame)+'\n').encode()
        self.app.dead = self.dead
        return len(data)

    def intent(self, binding):
        self.intents.append(binding)
        self.clock.mach += self.intent_delay
        self.clock.unix += self.intent_delay

    def observe(self, ticket, deadline, channel=None):
        self.exits.append(self.clock.mach)
        return self.app.dead

    def run(self, bound):
        path = self.directory / 'one'
        path.mkdir(mode=0o700)
        job = dict(acquisition_id='11111111-1111-4111-8111-111111111111', recipe=preview(bound)['recipe'], recipe_hash=preview(bound)['recipe_hash'])
        return self.backend.run_job(job, path, threading.Event(), self.intent)


@pytest.fixture
def idle(monkeypatch):
    # Select only reports readable when bytes exist; writable is always ready.
    def install(transport):
        monkeypatch.setattr(custody, 'select', NS(select=lambda r,w,e,t: ([x for x in r if x is transport and (transport.incoming or t > 0)], w, e)))
    return install


@pytest.mark.parametrize('stage', ['timely', 'ready', 'intent', 'assignment_ready', 'assignment_intent'])
def test_actual_backend_451_ready_and_intent_gates(tmp_path, bound, clock, monkeypatch, idle, stage):
    clock.mach = 419 if stage in ('ready', 'intent') else 100
    if stage.startswith('assignment'):
        clock.unix = 1099
    transport = InertTransport(tmp_path, clock, monkeypatch,
                              ready_delay=0 if stage.endswith('intent') else 2,
                              intent_delay=2 if stage.endswith('intent') else 0)
    idle(transport)
    result = transport.run(bound)
    assert transport.request['schema'] == 'software-pen-custody/v2'
    assert len(transport.request) == 16
    for key, value in bound['emission_deadlines'].items():
        assert transport.request[key] == value
    assert transport.request['prepare_timeout_seconds'] == 10
    assert transport.request['session_timeout_seconds'] == 30
    if stage == 'timely':
        assert transport.commands == ['authorize'] and result['outcome'] == 'completed'
    else:
        assert transport.commands == ['cancel']
        assert len(transport.intents) == int(stage.endswith('intent'))
        assert result['outcome'] == 'UNKNOWN'
        assert result['error'] == ('original assignment expired' if stage.startswith('assignment') else 'fixture posting cutoff expired')
        assert result['quiescent'] and result['release_attempted']
        assert transport.exits


@pytest.mark.parametrize('lost', [False, True])
def test_actual_coordinator_acquire_expiry_exact_lease(tmp_path, bound, clock, lost):
    calls = []
    class Backend:
        supports_emission_deadlines = True
        def validate(self): pass
        def run_job(self, *args): pytest.fail('Expired lease must not launch backend')
    def control(action, params):
        calls.append((action, params))
        if action == 'acquire':
            clock.unix = 1100
            if lost:
                raise RuntimeError('lost acquire acknowledgement')
            return {'acquired': True, 'state': {'identity': {'pid': 123}}}
        return {'released': True}
    jobs = PenJobs(tmp_path, Backend(), control)
    jobs.execute('one', bound, preview(bound)['recipe_hash'])
    result = wait(jobs)
    assert result['recipe']['emission_deadlines'] == bound['emission_deadlines']
    assert result['lease_held'] is lost
    assert result['state'] == ('UNKNOWN' if lost else 'refused')
    assert [c[0] for c in calls] == (['acquire'] if lost else ['acquire', 'release'])
    if not lost:
        assert calls[0][1]['acquisition_id'] == calls[1][1]['acquisition_id']
    changed = copy.deepcopy(bound)
    changed['emission_deadlines']['assignment_expires_unix'] = 1200
    with pytest.raises(ValueError, match='conflicts'):
        jobs.execute('one', changed, preview(changed)['recipe_hash'])
    close(jobs)


def test_454_acquire10_return419_ready421(tmp_path, bound, clock, monkeypatch, idle):
    transport = InertTransport(tmp_path, clock, monkeypatch)
    idle(transport)
    control = Control()
    def delayed(action, params):
        response = control(action, params)
        if action == 'acquire': clock.mach = 419
        return response
    jobs = PenJobs(tmp_path / 'store', transport.backend, delayed)
    jobs.execute('one', bound, preview(bound)['recipe_hash'])
    result = wait(jobs)
    assert transport.commands == ['cancel']
    assert 'authorization_intent' not in result
    assert result['state'] == 'UNKNOWN' and not result['lease_held']
    assert control.calls == ['acquire', 'release']
    close(jobs)


def test_admission_expiry_persisted_and_no_legacy_fallback(tmp_path, bound, clock):
    clock.mach = 420
    backend = NS(supports_emission_deadlines=True, validate=lambda: pytest.fail('expired'))
    jobs = PenJobs(tmp_path, backend, lambda *a: pytest.fail('expired'))
    result = jobs.execute('one', bound, preview(bound)['recipe_hash'])
    assert result['state'] == 'refused' and not result['lease_held']
    clock.mach = 1
    assert jobs.execute('one', bound, preview(bound)['recipe_hash']) == jobs.status('one')
    jobs.backend = QuartzBackend('/unused', 'unused')
    with pytest.raises(ValueError, match='v2'):
        jobs.execute('two', bound, preview(bound)['recipe_hash'])
    close(jobs)


def test_expiry_cleanup_finished_and_exit_are_not_gated(tmp_path, bound, clock, monkeypatch, idle):
    clock.mach = 419
    transport = InertTransport(tmp_path, clock, monkeypatch, ready_delay=0, finish_expired=True)
    idle(transport)
    result = transport.run(bound)
    assert transport.commands == ['authorize']
    assert result['outcome'] == 'UNKNOWN' and result['release_attempted'] and result['quiescent']
    assert result['finished']['authorized'] is True
    assert transport.exits == [421]


def test_expiry_keeps_unresolved_application(tmp_path, bound, clock, monkeypatch, idle):
    clock.mach = 419
    transport = InertTransport(tmp_path, clock, monkeypatch, dead=False)
    idle(transport)
    result = transport.run(bound)
    assert result['outcome'] == 'UNKNOWN' and not result['quiescent'] and not result['release_attempted']
    assert transport.backend.owners


def test_interrupted_public_await_keeps_actual_job_and_app_custody(tmp_path, bound, clock, monkeypatch, idle):
    import asyncio
    from blender_mcp import sculpt_tools
    transport = InertTransport(tmp_path, clock, monkeypatch)
    idle(transport)
    entered, resume = threading.Event(), threading.Event()
    original_recv = transport.recv
    def held_recv(count):
        entered.set()
        assert resume.wait(3)
        return original_recv(count)
    monkeypatch.setattr(transport, 'recv', held_recv)
    jobs = PenJobs(tmp_path / 'store', transport.backend, Control())
    monkeypatch.setattr(sculpt_tools, 'PenJobs', lambda *a: jobs)
    monkeypatch.setattr(sculpt_tools, 'AppQuartzBackend', lambda *a, **k: transport.backend)
    for key, value in dict(BMCP_JOB_ROOT=str(tmp_path / 'store'), BMCP_PEN_APP='/inert/App.app',
                           BMCP_PEN_EXECUTABLE='/inert/App.app/Contents/MacOS/Pen', BMCP_PEN_SHA256='inert').items():
        monkeypatch.setenv(key, value)
    tools = {}
    class MCP:
        def tool(self):
            def register(function):
                tools[function.__name__] = function
                return function
            return register
    sculpt_tools.register(MCP(), lambda: None)
    class InterruptedWait:
        def __await__(self):
            yield 'caller awaiting dispatched work'
    async def to_thread(function, *args):
        function(*args)
        await InterruptedWait()
    monkeypatch.setattr(sculpt_tools.asyncio, 'to_thread', to_thread)
    monkeypatch.setattr(sculpt_tools.asyncio, 'get_running_loop', lambda: object())
    from blender_mcp.pen import Recipe
    call = tools['pen_execute']('one', Recipe.model_validate(bound), preview(bound)['recipe_hash'])
    assert call.send(None) == 'caller awaiting dispatched work'
    assert entered.wait(2)
    with pytest.raises(asyncio.CancelledError):
        call.throw(asyncio.CancelledError())
    assert jobs.status('one')['lease_held'] and transport.backend.owners
    assert not jobs.cancels['one'].is_set()
    resume.set()
    result = wait(jobs)
    assert result['state'] == 'completed' and not result['lease_held']
    assert transport.commands == ['authorize']
    close(jobs)


def test_expiry_immediately_before_launch_never_retains_unstarted_ticket(tmp_path, bound, clock, monkeypatch, idle):
    transport = InertTransport(tmp_path, clock, monkeypatch)
    idle(transport)
    monkeypatch.setattr(custody, 'launch_diagnostic', lambda *a, **k: setattr(clock, 'mach', 420))
    result = transport.run(bound)
    assert not transport.backend.owners and not hasattr(transport, 'request')
    assert result['release_attempted'] and result['quiescent']
    assert result['error'] == 'fixture posting cutoff expired'


def test_expiry_during_authorize_writable_wait_stops_send_but_allows_cleanup(bound, clock, monkeypatch):
    sent = []
    connection = NS(setblocking=lambda _: None, set_inheritable=lambda _: None,
                    send=lambda data: sent.append(bytes(data)) or len(data))
    channel = custody.Channel(connection, {}, 450, threading.Event())
    channel.emission_authority = deadlines.EmissionAuthority(bound)
    def ready(*args):
        clock.mach = 420
        return [], [connection], []
    monkeypatch.setattr(custody, 'select', NS(select=ready))
    with pytest.raises(deadlines.DeadlineExpired):
        channel.write_frame('authorize')
    assert not sent
    channel.write_frame('cancel', cleanup=True)
    assert json.loads(sent[0]) == {'kind': 'cancel'}


def test_actual_legacy_backend_wire_stays_v1(tmp_path, recipe, clock, monkeypatch, idle):
    transport = InertTransport(tmp_path, clock, monkeypatch)
    idle(transport)
    result = transport.run(recipe)
    assert result['outcome'] == 'completed'
    assert transport.request['schema'] == 'software-pen-custody/v1'
    assert len(transport.request) == 14


def test_worker_admission_expiry_does_not_acquire(tmp_path, bound, clock, monkeypatch):
    monkeypatch.setattr(threading.Thread, 'start', lambda self: None)
    backend = NS(supports_emission_deadlines=True, validate=lambda: None)
    jobs = PenJobs(tmp_path, backend, lambda *a: pytest.fail('Expired worker must not acquire'))
    jobs.execute('one', bound, preview(bound)['recipe_hash'])
    clock.mach = 420
    jobs._run('one', threading.Event())
    assert jobs.status('one')['state'] == 'refused'
    assert not jobs.status('one')['lease_held']
    close(jobs)


def test_expiry_cannot_disguise_reused_application_owner(tmp_path, bound, clock, monkeypatch, idle):
    transport = InertTransport(tmp_path, clock, monkeypatch)
    idle(transport)
    clock.mach = 420
    owner = object()
    transport.backend.owners['11111111-1111-4111-8111-111111111111'] = owner
    with pytest.raises(ValueError, match='reused acquisition'):
        transport.run(bound)
    assert list(transport.backend.owners.values()) == [owner]


@pytest.mark.parametrize('field', ['fixture_posting_cutoff_mach_seconds', 'assignment_expires_unix'])
@pytest.mark.parametrize('value', [10**400, 10**309, 2**1024, 10**308, int(sys.float_info.max), 2**63, 2**64-1,
                                   float('inf'), float('-inf'), float('nan')],
                         ids=['int400', 'int309', 'double-overflow', 'int308', 'max-double-int',
                              'signed64-overflow', 'unsigned64', 'infinity', 'negative-infinity', 'nan'])
def test_non_double_deadlines_refused_before_acquisition(tmp_path, bound, field, value):
    bound['emission_deadlines'][field] = value
    with pytest.raises(ValueError):
        Recipe.model_validate(bound)  # The typed public preview/execute input.
    with pytest.raises(ValueError):
        preview(bound)
    def forbidden(*args):
        pytest.fail('Invalid public deadline reached acquisition or backend')
    backend = NS(supports_emission_deadlines=True, validate=forbidden, run_job=forbidden)
    jobs = PenJobs(tmp_path, backend, forbidden)
    try:
        with pytest.raises(ValueError):
            jobs.execute('overflow', bound, '0' * 64)
        assert jobs.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 0
        assert not jobs.threads
    finally:
        close(jobs)


@pytest.mark.parametrize('field', ['fixture_posting_cutoff_mach_seconds', 'assignment_expires_unix'])
@pytest.mark.parametrize('value', [1, 420, 1.25, 420.0, 2**53+1, 2**63-1,
                                   sys.float_info.max, float.fromhex('0x0.0000000000001p-1022')],
                         ids=['one', 'ordinary-int', 'fraction', 'ordinary-float', 'int53-plus-one',
                              'signed64-max', 'largest-double', 'smallest-positive-double'])
def test_finite_double_deadlines_preserve_original_values_and_hash(bound, field, value):
    bound['emission_deadlines'][field] = value
    prepared = preview(bound)
    actual = prepared['recipe']['emission_deadlines'][field]
    assert type(actual) is type(value) and actual == value
    assert canonical(prepared['recipe']) == canonical(bound)
    assert prepared['recipe_hash'] == recipe_hash(bound)


def run_deadline_parser_matrix(parser, output):
    """Explicit opt-in inert parser check; never run by the ordinary pytest suite.

    parser must be the unchanged native definitions + ControlledRequest-only main,
    compiled SOFTWARE_PEN_INERT_BUILD and checked for no CGEventPost import.
    """
    import subprocess
    output = Path(output).resolve()
    cases = [('ordinary-int', 420), ('ordinary-float', 420.25), ('int53-plus-one', 2**53+1),
             ('signed64-max', 2**63-1), ('signed64-plus-one', 2**63), ('unsigned64-max', 2**64-1),
             ('int165', 10**165), ('int166', 10**166), ('int308', 10**308),
             ('max-double-int', int(sys.float_info.max)), ('int400', 10**400),
             ('max-double-float', sys.float_info.max),
             ('min-positive-float', float.fromhex('0x0.0000000000001p-1022'))]
    rows = []
    baseline = None
    for field in ('fixture_posting_cutoff_mach_seconds', 'assignment_expires_unix'):
        for label, value in cases:
            payload = recipe.__wrapped__()
            payload['emission_deadlines'] = dict(fixture_posting_cutoff_mach_seconds=420., assignment_expires_unix=1100.)
            payload['emission_deadlines'][field] = value
            try:
                preview(payload)
                accepted = True
            except ValueError:
                accepted = False
            with pytest.MonkeyPatch.context() as patch:
                fake_clock = clock.__wrapped__(patch)
                fake_clock.mach = fake_clock.unix = 0
                directory = output / ('m' + str(len(rows)))
                directory.mkdir(mode=0o700)
                if accepted:
                    transport = InertTransport(directory, fake_clock, patch, ready_delay=0)
                    idle.__wrapped__(patch)(transport)
                    result = transport.run(payload)
                    assert result['outcome'] == 'completed', result
                    request_path = directory / 'one' / 'request.json'
                    raw = request_path.read_bytes()
                    baseline = (raw, request_path)
                    assert json.loads(raw)[field] == value
                else:
                    # Refused inputs cannot produce a request. Probe the parser by
                    # replacing only the numeric token in a real producer request.
                    raw, request_path = baseline
                    obj = json.loads(raw)
                    obj[field] = value
                    raw = canonical(obj).encode()
                input_path = directory / 'parser-input.json'
                input_path.write_bytes(raw)
                native = subprocess.run([str(parser), str(input_path), str(request_path)],
                                        capture_output=True, text=True, check=True).stdout.strip()
                row = dict(field=field, case=label, token=canonical(value), public_accepted=accepted,
                           exact_producer_bytes=accepted, native=native)
                rows.append(row)
                (output / 'producer-parser-matrix.json').write_text(json.dumps(rows, indent=2))
                expected_public = label in {'ordinary-int', 'ordinary-float', 'int53-plus-one',
                                            'signed64-max', 'max-double-float', 'min-positive-float'}
                assert accepted is expected_public, row
                expected_native = label not in {'int166', 'int308', 'max-double-int', 'int400'}
                assert (native == 'ACCEPT') is expected_native, row
    return rows
