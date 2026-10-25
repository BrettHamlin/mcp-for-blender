"""Durable at-most-once dispatch, external to Blender's main thread."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading
import time

from .pen import canonical, preview


class PenJobs:
    def __init__(self, root, backend, control):
        try:
            import fcntl
        except ImportError:
            raise RuntimeError('Pen coordinator requires POSIX; Quartz backend requires macOS')
        self.root = Path(root).resolve()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.owner = (self.root / 'coordinator.lock').open('a')
        try:
            fcntl.flock(self.owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.owner.close()
            raise RuntimeError('Another coordinator owns this job directory')
        self.db = sqlite3.connect(self.root / 'jobs.sqlite3', check_same_thread=False)
        self.db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        self.backend = backend
        self.control = control
        self.lock = threading.RLock()
        self.cancels = {}
        self.threads = {}
        for (payload,) in self.db.execute('SELECT payload FROM jobs').fetchall():
            job = json.loads(payload)
            if job['state'] in ('pending', 'running'):
                job.update(state='UNKNOWN', error='Coordinator restarted; no automatic replay', lease_held=True)
                self._save(job)

    def _save(self, job):
        job['updated_at'] = time.time()
        self.db.execute('INSERT OR REPLACE INTO jobs VALUES (?, ?)', (job['request_id'], canonical(job)))
        self.db.commit()

    def status(self, request_id):
        with self.lock:
            row = self.db.execute('SELECT payload FROM jobs WHERE id=?', (request_id,)).fetchone()
            if row is None:
                raise ValueError('Unknown request_id')
            return json.loads(row[0])

    def execute(self, request_id, payload, expected_recipe_hash):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', request_id):
            raise ValueError('Invalid request_id')
        prepared = preview(payload)
        if expected_recipe_hash != prepared['recipe_hash']:
            raise ValueError('Recipe changed since preview')
        recipe = prepared['recipe']
        with self.lock:
            try:
                prior = self.status(request_id)
            except ValueError:
                prior = None
            if prior:
                if prior['recipe_hash'] != prepared['recipe_hash']:
                    raise ValueError('request_id conflicts with existing payload')
                return prior
            for (data,) in self.db.execute('SELECT payload FROM jobs'):
                other = json.loads(data)
                if other['lease_held']:
                    raise ValueError('A live or unresolved pen job owns this coordinator')
            self.backend.validate()
            job = {'schema': 'bmcp-job/v1', 'request_id': request_id,
                   'recipe': recipe, 'recipe_hash': prepared['recipe_hash'],
                   'state': 'pending', 'lease_held': True,
                   'evidence': {key: 'UNKNOWN' for key in ('posted', 'received', 'applied', 'form_achieved', 'release')}}
            # Persist BEFORE any remote mutation. A lost acquire response is UNKNOWN.
            self._save(job)
            cancel = self.cancels[request_id] = threading.Event()
            thread = self.threads[request_id] = threading.Thread(target=self._run, args=(request_id, cancel), daemon=True)
            thread.start()
            return copy.deepcopy(job)

    def _run(self, request_id, cancel):
        job = self.status(request_id)
        recipe = job['recipe']
        try:
            acquired = self.control('acquire', {'session': recipe['session'],
                'expected_binding': recipe['expected_binding'], 'request_id': request_id})
            if acquired.get('acquired') is not True:
                raise RuntimeError('Missing lease acknowledgement')
            if acquired['state']['identity']['pid'] != recipe['target_pid']:
                released = self.control('release', {'session': recipe['session'], 'request_id': request_id})
                if released.get('released') is not True:
                    raise RuntimeError('Missing lease release acknowledgement')
                with self.lock:
                    job.update(state='refused', lease_held=False, error='Wrong target PID')
                    self._save(job)
                return
            with self.lock:
                job['state'] = 'running'
                self._save(job)
            directory = self.root / request_id
            directory.mkdir(mode=0o700)
            if cancel.is_set():
                result = {'quiescent': True, 'outcome': 'cancelled',
                          'release_attempted': True, 'release': 'not started'}
            else:
                result = self.backend.run(recipe, directory, cancel)
            job['backend'] = result
            job['evidence'].update({k: result[k] for k in job['evidence'] if k in result})
            if result.get('quiescent') is True and result.get('release_attempted') is True:
                released = self.control('release', {'session': recipe['session'], 'request_id': request_id})
                if released.get('released') is not True:
                    raise RuntimeError('Missing lease release acknowledgement')
                job['after'] = released.get('state')
                job['lease_held'] = False
            job['state'] = result.get('outcome', 'UNKNOWN') if not job['lease_held'] else 'UNKNOWN'
        except Exception as error:
            job.update(state='UNKNOWN', error=str(error))
        with self.lock:
            if cancel.is_set():
                job['cancel_requested'] = True
            self._save(job)

    def cancel(self, request_id):
        with self.lock:
            job = self.status(request_id)
            event = self.cancels.get(request_id)
            if event is not None and self.threads[request_id].is_alive():
                event.set()
                job['cancel_requested'] = True
                self._save(job)
            else:
                job['cancel_note'] = 'No live process handle; release UNKNOWN; no replay or PID signal'
            return job

    def reconcile(self, request_id, native_release_verified, note):
        """Explicit external custody evidence, never an automatic timeout retry."""
        with self.lock:
            job = self.status(request_id)
            if self.threads.get(request_id) and self.threads[request_id].is_alive():
                raise ValueError('Job still running')
            if not job['lease_held']:
                return job
            if native_release_verified is not True or not isinstance(note, str) or not note.strip():
                raise ValueError('External process-exit and native-release verification note required')
            response = self.control('release', {'session': job['recipe']['session'], 'request_id': request_id})
            if response.get('released') is not True:
                raise ValueError('Lease release not acknowledged')
            job.update(lease_held=False, reconciliation={'external_claim': note,
                'native_release_verified_by_caller': True})
            self._save(job)
            return job

    def record_evidence(self, request_id, layer, verdict, artifact, sha256, reviewer):
        """Attach a hash-verified external review; it is not machine receiver proof."""
        if layer not in ('received', 'applied', 'form_achieved') or verdict not in ('PASS', 'FAIL', 'UNKNOWN'):
            raise ValueError('Invalid evidence layer or verdict')
        if not isinstance(reviewer, str) or not reviewer.strip():
            raise ValueError('Reviewer/provenance required')
        with self.lock:
            job = self.status(request_id)
            if self.threads.get(request_id) and self.threads[request_id].is_alive():
                raise ValueError('Attach review after job settles')
            if not artifact or Path(artifact).name != artifact:
                raise ValueError('Evidence artifact must be a basename in the job directory')
            directory = self.root / request_id
            path = directory / artifact
            if path.is_symlink() or path.resolve().parent != directory or not path.is_file():
                raise ValueError('Evidence artifact missing or outside job directory')
            if hashlib.sha256(path.read_bytes()).hexdigest() != sha256:
                raise ValueError('Evidence artifact hash mismatch')
            review = {'layer': layer, 'verdict': verdict, 'artifact': artifact,
                      'sha256': sha256, 'reviewer': reviewer,
                      'authority': 'external review; not independently validated by coordinator'}
            reviews = job.setdefault('external_reviews', [])
            if review not in reviews:
                reviews.append(review)
            self._save(job)
            return job
