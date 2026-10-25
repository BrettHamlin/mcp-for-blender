"""Private software-pen-custody/v1 controller; no UI or process-ID signalling."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import select
import socket
import stat
import tempfile
import threading
import time
import uuid

from .pen import canonical, recipe_hash
from .pen_app import DarwinApplications, LaunchTicket, launch_diagnostic

SCHEMA = 'software-pen-custody/v1'
LIMIT = 8192
COMMON = {'schema', 'acquisition_id', 'challenge', 'request_sha256', 'recipe_sha256', 'stroke_sha256'}
FINISHED = {'kind', 'emitter_pid', 'emitter_audit_token', 'outcome', 'authorized',
            'receipt_path', 'receipt_sha256', 'evidence_complete', 'post_calls_observed',
            'sender_release_observed', 'native_release', 'error'}
RECEIPT_LIMIT = 4_000_000  # Native cap includes the final accounting line.

READY = {'kind', 'emitter_pid', 'emitter_audit_token', 'controller_peer_audit_token',
         'emission_enabled', 'posted'}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def private_directory(path):
    path = Path(path)
    if not path.is_absolute() or path != path.resolve():
        raise ValueError('Custody paths must be canonical absolute paths without symlinks')
    for item in (path, *path.parents):
        if item.is_symlink():
            raise ValueError('Symlink ancestor refused')
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError('Custody directory must be owned and mode 0700')
    return path


def persist(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def strict_object(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate JSON key')
            result[key] = value
        return result
    def constant(value):
        raise ValueError('Nonfinite JSON number')
    def number(value):
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError('Nonfinite JSON number')
        return parsed
    value = json.loads(data.decode('utf-8', errors='strict'), object_pairs_hook=pairs,
                       parse_constant=constant, parse_float=number)
    if type(value) is not dict:
        raise ValueError('Expected JSON object')
    return value


class Cancelled(RuntimeError):
    pass


class Channel:
    def __init__(self, connection, binding, deadline, cancel):
        self.connection = connection
        connection.setblocking(False)
        connection.set_inheritable(False)
        self.binding = binding
        self.deadline = deadline
        self.cancel = cancel
        self.buffer = bytearray()

    def check(self):
        if self.cancel.is_set():
            raise Cancelled('Cancellation requested')
        if time.monotonic() >= self.deadline:
            raise TimeoutError('Custody deadline expired')

    def read(self):
        while b'\n' not in self.buffer:
            self.check()
            if len(self.buffer) >= LIMIT:
                raise ValueError('Oversized custody frame')
            if not select.select([self.connection], [], [], .02)[0]:
                continue
            chunk = self.connection.recv(LIMIT - len(self.buffer))
            if not chunk:
                raise EOFError('Custody disconnected')
            self.buffer.extend(chunk)
        line, _, rest = self.buffer.partition(b'\n')
        self.buffer = bytearray(rest)
        if len(line) + 1 > LIMIT:
            raise ValueError('Oversized custody frame')
        frame = strict_object(line)
        if any(type(frame.get(k)) is not str or frame[k] != v for k, v in self.binding.items()):
            raise ValueError('Stale or invalid custody binding')
        return frame

    def require_idle(self):
        self.check()
        if self.buffer or select.select([self.connection], [], [], 0)[0]:
            raise ValueError('Unexpected frame or disconnect before AUTHORIZE')

    def write_frame(self, kind, *, cleanup=False):
        data = memoryview((canonical(dict(self.binding, kind=kind)) + '\n').encode())
        deadline = min(self.deadline, time.monotonic() + .2) if not cleanup else time.monotonic() + .2
        while data:
            if not cleanup:
                self.check()
            if time.monotonic() >= deadline:
                raise TimeoutError('Custody send deadline')
            if select.select([], [self.connection], [], .02)[1]:
                count = self.connection.send(data)
                if not count:
                    raise EOFError('Custody disconnected')
                data = data[count:]


def verify_ready(frame, binding, controller_token, enabled):
    if set(frame) != COMMON | READY or frame.get('kind') != 'ready':
        raise ValueError('Expected exact READY frame')
    if any(type(frame[k]) is not str or frame[k] != binding[k] for k in COMMON):
        raise ValueError('READY binding mismatch')
    if type(frame['emitter_pid']) is not int or not 1 <= frame['emitter_pid'] <= 2147483647:
        raise ValueError('Invalid emitter PID')
    token = frame['emitter_audit_token']
    if type(token) is not str or len(token) != 64 or any(c not in '0123456789abcdef' for c in token):
        raise ValueError('Invalid emitter audit token')
    if frame['controller_peer_audit_token'] != controller_token:
        raise ValueError('Controller peer token mismatch')
    if type(frame['emission_enabled']) is not bool or frame['emission_enabled'] is not enabled or frame['posted'] is not False:
        raise ValueError('READY emission state mismatch')


def hex64(value):
    return type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def verify_finished(frame, binding, ready, receipt_path, authorization_sent, cancel_sent):
    if set(frame) != COMMON | FINISHED or frame.get('kind') != 'finished':
        raise ValueError('Expected exact FINISHED frame')
    if any(type(frame[k]) is not str or frame[k] != binding[k] for k in COMMON):
        raise ValueError('FINISHED binding mismatch')
    if type(frame['emitter_pid']) is not int or frame['emitter_pid'] != ready['emitter_pid'] or \
            frame['emitter_audit_token'] != ready['emitter_audit_token']:
        raise ValueError('FINISHED emitter identity mismatch')
    for key in ('authorized', 'evidence_complete', 'sender_release_observed'):
        if type(frame[key]) is not bool:
            raise ValueError('FINISHED requires strict booleans')
    if type(frame['post_calls_observed']) is not int or frame['post_calls_observed'] < 0:
        raise ValueError('Invalid post count')
    if frame['outcome'] not in ('completed', 'cancelled', 'unknown') or frame['native_release'] != 'UNKNOWN':
        raise ValueError('Invalid final outcome/native release')
    if frame['error'] is not None and (type(frame['error']) is not str or not frame['error']):
        raise ValueError('Invalid final error')
    if frame['receipt_path'] is not None and (type(frame['receipt_path']) is not str or frame['receipt_path'] != str(receipt_path)):
        raise ValueError('Unexpected receipt path')
    if frame['receipt_sha256'] is not None and not hex64(frame['receipt_sha256']):
        raise ValueError('Invalid receipt hash')
    if (frame['receipt_path'] is None) != (frame['receipt_sha256'] is None):
        raise ValueError('Incomplete receipt metadata')
    if frame['authorized'] and not authorization_sent:
        raise ValueError('Unexpected authorization consumption')
    if not frame['authorized'] and (frame['post_calls_observed'] or frame['sender_release_observed']):
        raise ValueError('Post observations without authorization')
    if frame['outcome'] == 'cancelled' and not cancel_sent:
        raise ValueError('Unrequested cancellation outcome')
    if frame['outcome'] == 'completed' and (not frame['authorized'] or not frame['evidence_complete'] or
            not frame['sender_release_observed'] or frame['error'] is not None):
        raise ValueError('Incomplete completed outcome')
    if not frame['evidence_complete'] and frame['outcome'] != 'unknown':
        raise ValueError('Incomplete evidence requires unknown outcome')
    if frame['receipt_path'] is None:
        if frame['evidence_complete']:
            raise ValueError('Complete receipt missing')
        return
    private_directory(receipt_path.parent)
    fd = os.open(receipt_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or \
                stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or info.st_size > RECEIPT_LIMIT:
            raise ValueError('Unsafe or oversized receipt')
        data = stream.read(RECEIPT_LIMIT + 1)
    if len(data) > RECEIPT_LIMIT or digest(data) != frame['receipt_sha256']:
        raise ValueError('Receipt hash/size mismatch')
    if not frame['evidence_complete']:
        return  # Partial bytes remain available, never interpreted as complete release.
    lines = data.splitlines()
    if not data.endswith(b'\n') or not 1 <= len(lines) <= 1212:
        raise ValueError('Receipt record bounds/trailing LF mismatch')
    rows = [strict_object(line) for line in lines]
    records, status = rows[:-1], rows[-1]
    status_keys = {'log_status', 'evidence_complete', 'buffered_records', 'dropped_records',
                   'outcome', 'sender_release_observed', 'post_calls_observed'}
    if set(status) != status_keys or status['log_status'] != 'complete' or status['evidence_complete'] is not True:
        raise ValueError('Missing complete receipt accounting')
    for key, expected in (('buffered_records', len(records)), ('dropped_records', 0),
                          ('post_calls_observed', frame['post_calls_observed'])):
        if type(status[key]) is not int or status[key] != expected:
            raise ValueError('Receipt accounting mismatch')
    if status['outcome'] != frame['outcome'] or type(status['sender_release_observed']) is not bool or \
            status['sender_release_observed'] != frame['sender_release_observed']:
        raise ValueError('Receipt summary mismatch')
    if any('log_status' in row or ('post_called' in row and type(row['post_called']) is not bool) for row in records):
        raise ValueError('Invalid receipt record')
    posts = [row for row in records if row.get('post_called') is True]
    def zero(row, key):
        return type(row.get(key)) in (int, float) and row[key] == 0
    up = any(row.get('kind') == 'contact_end' and all(zero(row, k) for k in
             ('cg_pressure', 'mouse_pressure', 'buttons')) for row in posts)
    exited = any(row.get('kind') == 'proximity_exit' and zero(row, 'enter') for row in posts)
    if len(posts) != frame['post_calls_observed'] or (up and exited) != frame['sender_release_observed']:
        raise ValueError('Receipt post/release observations mismatch')


class AppQuartzBackend:
    """Retained application owners survive metadata failure and cancelled MCP awaits."""
    def __init__(self, app_path, executable, sha256, *, allow_emission=False, applications=None, main_loop=None):
        self.app_path = Path(app_path)
        self.executable = Path(executable)
        self.sha256 = sha256
        if type(allow_emission) is not bool:
            raise ValueError('allow_emission must be an explicit boolean')
        self.allow_emission = allow_emission
        self.applications = applications
        self.main_loop = main_loop
        self.owners = {}
        self.prepare_seconds = 10
        self.session_seconds = 30
        self.cleanup_seconds = 5

    def validate(self):
        if self.applications is None:
            self.applications = DarwinApplications(self.main_loop)
        if not self.app_path.is_absolute() or self.app_path != self.app_path.resolve() or not self.app_path.is_dir():
            raise ValueError('Configure the canonical existing application bundle')
        if self.executable != self.executable.resolve() or self.executable.parent.parent.parent != self.app_path:
            raise ValueError('Executable must be canonical inside the configured app Contents/MacOS')
        if self.executable.parent.name != 'MacOS' or self.executable.parent.parent.name != 'Contents':
            raise ValueError('Invalid application executable path')
        self.check_executable()

    def check_executable(self):
        if not self.executable.is_file() or not os.access(self.executable, os.X_OK) or digest(self.executable.read_bytes()) != self.sha256:
            raise ValueError('Configured executable changed or unavailable')

    def authenticate(self, app, connection, ready):
        apps = self.applications
        pid = apps.pid(app)
        if apps.terminated(app) or not apps.same(app, apps.fresh(pid)):
            raise ValueError('Retained application stale or terminated')
        if apps.executable(app) != self.executable:
            raise ValueError('Application executable mismatch')
        self.check_executable()
        uid, peer_pid, token = apps.peer(connection)
        if uid != os.getuid() or peer_pid != pid or ready['emitter_pid'] != pid or token != ready['emitter_audit_token']:
            raise ValueError('Kernel peer attribution mismatch')
        # Lookup again after dynamic credentials; neither a JSON PID nor the
        # last_pid socket token independently establishes lifetime ownership.
        if apps.terminated(app) or not apps.same(app, apps.fresh(pid)):
            raise ValueError('Application identity changed during peer check')

    def _observe_exit(self, ticket, deadline, channel=None):
        while time.monotonic() < deadline:
            if channel is not None:
                if channel.buffer:
                    raise ValueError('Trailing custody frame after FINISHED')
                if select.select([channel.connection], [], [], 0)[0]:
                    if channel.connection.recv(LIMIT):
                        raise ValueError('Trailing custody data after FINISHED')
                    channel = None  # EOF is not exit evidence.
            if ticket.completed.is_set() and ticket.application is not None:
                try:
                    if self.applications.terminated(ticket.application):
                        return True
                except Exception:
                    pass
            time.sleep(.02)
        return False

    def _retain_until_exit(self, acquisition, ticket):
        # Read-only lifetime supervision persists after a bounded job response.
        # Never signals a PID or drops late launch callback ownership.
        while True:
            if self._observe_exit(ticket, time.monotonic() + 1):
                self.owners.pop(acquisition, None)
                return
            if ticket.completed.is_set() and ticket.application is None and ticket.error:
                return  # Keep the failed/ambiguous ticket for external reconciliation.

    def run_job(self, job, directory, cancel, persist_intent):
        self.validate()
        acquisition = job['acquisition_id']
        if str(uuid.UUID(acquisition)) != acquisition or acquisition in self.owners:
            raise ValueError('Invalid or reused acquisition')
        recipe = job['recipe']
        if recipe_hash(recipe) != job['recipe_hash']:
            raise ValueError('Recipe binding changed')
        directory = private_directory(directory)
        stroke = canonical(recipe['stroke']).encode()
        if len(stroke) > 2_000_000:
            raise ValueError('Stroke exceeds evidence limit')
        persist(directory / 'stroke.json', stroke)
        socket_dir = Path(tempfile.mkdtemp(prefix='bmcp-', dir='/private/tmp'))
        private_directory(socket_dir)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.set_inheritable(False)
        connection = channel = ticket = None
        authenticated = False
        authorization_sent = cancel_sent = False
        result = {'quiescent': False, 'outcome': 'UNKNOWN', 'release': 'UNKNOWN',
                  'release_attempted': False, 'posted': 'UNKNOWN'}
        started = time.monotonic()
        deadline = started + self.session_seconds
        try:
            socket_path = socket_dir / 's'
            listener.bind(str(socket_path))
            socket_path.chmod(0o600)
            listener.listen(1)
            listener.setblocking(False)
            controller_token = self.applications.own_token()
            request = dict(schema=SCHEMA, acquisition_id=acquisition, challenge=secrets.token_hex(32),
                recipe_sha256=job['recipe_hash'], stroke_path=str(directory / 'stroke.json'),
                stroke_sha256=digest(stroke), target_pid=recipe['target_pid'], bounds=recipe['bounds'],
                socket_path=str(socket_path), controller_pid=os.getpid(), controller_audit_token=controller_token,
                prepare_timeout_seconds=self.prepare_seconds, session_timeout_seconds=self.session_seconds,
                receipt_path=str(directory / 'receipt.jsonl'))
            request_bytes = canonical(request).encode()
            request_path = directory / 'request.json'
            persist(request_path, request_bytes)
            binding = {key: request[key] for key in COMMON - {'request_sha256'}}
            binding['request_sha256'] = digest(request_bytes)
            if cancel.is_set():
                return dict(result, quiescent=True, outcome='cancelled', release_attempted=True, release='not started')
            ticket = self.owners[acquisition] = LaunchTicket()
            arguments = ['--prepare-session', str(request_path)]
            if self.allow_emission:
                arguments.append('--allow-authorized-emission')
            launch_diagnostic('launch_requested', ticket=id(ticket), started=started,
                              prepare_deadline=min(deadline, started + self.prepare_seconds))
            self.applications.launch(ticket, self.app_path, arguments)
            prepare_deadline = min(deadline, started + self.prepare_seconds)
            launch_diagnostic('ticket_wait_enter', ticket=id(ticket), prepare_deadline=prepare_deadline)
            while not ticket.completed.is_set():
                self.applications.refresh()
                if cancel.is_set() or time.monotonic() >= prepare_deadline:
                    launch_diagnostic('ticket_wait_rejected', ticket=id(ticket),
                                      completed=ticket.completed.is_set(), cancelled=cancel.is_set(),
                                      prepare_deadline=prepare_deadline)
                    raise TimeoutError('Launch completion missing or cancelled')
                ticket.completed.wait(.02)
            launch_diagnostic('ticket_wait_return', ticket=id(ticket))
            if ticket.error or ticket.application is None:
                raise RuntimeError(ticket.error or 'Launch returned no retained application')
            app = ticket.application
            persist(directory / 'application.json', canonical({'pid': self.applications.pid(app),
                'acquisition_id': acquisition, 'executable': str(self.executable), 'sha256': self.sha256}).encode())
            while connection is None:
                if cancel.is_set() or time.monotonic() >= prepare_deadline:
                    raise TimeoutError('READY connection missing or cancelled')
                if select.select([listener], [], [], .02)[0]:
                    connection, _ = listener.accept()
                    launch_diagnostic('connection_accepted', ticket=id(ticket))
            listener.close()  # Exactly one connection; no reconnect.
            channel = Channel(connection, binding, prepare_deadline, cancel)
            launch_diagnostic('ready_read_enter', ticket=id(ticket))
            ready = channel.read()
            launch_diagnostic('ready_read_return', ticket=id(ticket))
            verify_ready(ready, binding, controller_token, self.allow_emission)
            self.authenticate(app, connection, ready)
            authenticated = True
            channel.deadline = deadline
            if not self.allow_emission:
                channel.write_frame('cancel', cleanup=True)
                cancel_sent = True
                channel.cancel = threading.Event()
            else:
                channel.require_idle()
                persist_intent(binding)  # Durable before dispatch, never retried.
                self.authenticate(app, connection, ready)
                channel.require_idle()
                channel.write_frame('authorize')
                authorization_sent = True
            try:
                frame = channel.read()
            except Cancelled:
                self.authenticate(app, connection, ready)
                channel.write_frame('cancel', cleanup=True)
                cancel_sent = True
                channel.cancel = threading.Event()
                channel.deadline = min(deadline, time.monotonic() + self.cleanup_seconds)
                frame = channel.read()
            verify_finished(frame, binding, ready, directory / 'receipt.jsonl', authorization_sent, cancel_sent)
            result['finished'] = frame
            result['quiescent'] = self._observe_exit(ticket, time.monotonic() + self.cleanup_seconds, channel)
            if result['quiescent'] and frame['evidence_complete'] and frame['outcome'] in ('completed', 'cancelled'):
                released = frame['sender_release_observed'] or (not frame['authorized'] and frame['post_calls_observed'] == 0)
                if released:
                    result.update(outcome=frame['outcome'], release_attempted=True,
                        release='emitter up/exit observed; native release UNKNOWN' if frame['sender_release_observed'] else 'not authorized; no sender posts reported',
                        post_calls_observed=frame['post_calls_observed'],
                        posted='observed; coverage unverified' if frame['post_calls_observed'] else 'UNKNOWN')
        except Exception as exc:
            result['error'] = str(exc)
            if channel is not None and authenticated:
                try:
                    self.authenticate(ticket.application, connection, ready)
                    channel.write_frame('cancel', cleanup=True)
                except Exception:
                    pass
        finally:
            # Endpoint cleanup cannot bypass lifetime custody if it fails.
            for cleanup in (lambda: connection.close() if connection is not None else None,
                            listener.close, lambda: (socket_dir / 's').unlink(missing_ok=True),
                            socket_dir.rmdir):
                try:
                    cleanup()
                except Exception as exc:
                    result.update(error=result.get('error') or str(exc), outcome='UNKNOWN', release_attempted=False)
            if ticket is not None:
                if not result['quiescent']:
                    result['quiescent'] = self._observe_exit(ticket, time.monotonic() + self.cleanup_seconds)
                if result['quiescent']:
                    self.owners.pop(acquisition, None)
                else:
                    threading.Thread(target=self._retain_until_exit, args=(acquisition, ticket), daemon=True).start()
        return result
