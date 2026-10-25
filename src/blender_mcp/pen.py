"""Versioned QuartzPen CLI adapter. Never builds, signs, focuses, or installs."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, field_validator, model_validator

FRAME = 'quartz-global: origin main-display top-left, x right, y down'
Number = StrictFloat | StrictInt


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class Sample(Contract):
    event: Literal['contact_begin', 'contact_move', 'contact_end']
    x: Number
    y: Number
    time: Number
    pressure: Number = Field(ge=0, le=1)


class Coordinates(Contract):
    frame: Literal['quartz-global: origin main-display top-left, x right, y down']
    units: Literal['points']


class Stroke(Contract):
    schema_version: Literal['software-pen-path-samples/v1'] = Field(alias='schema')
    scope: Literal['HOST_ONLY']
    coordinates: Coordinates
    time_units: Literal['seconds']
    pressure_range: tuple[Number, Number]
    duration: Number = Field(ge=0.01, le=10)
    samples: list[Sample] = Field(min_length=2, max_length=1201)

    @model_validator(mode='after')
    def sequence(self):
        if self.pressure_range != (0, 1):
            raise ValueError('pressure_range must be [0,1]')
        previous = None
        for i, point in enumerate(self.samples):
            kind = 'contact_begin' if i == 0 else 'contact_end' if i == len(self.samples)-1 else 'contact_move'
            if point.event != kind or not 0 <= point.time <= self.duration:
                raise ValueError('Invalid contact sequence')
            if previous is not None and point.time - previous < 1/240 - 1e-9:
                raise ValueError('Invalid time order or rate above 240Hz')
            previous = point.time
        if self.samples[0].time != 0 or self.samples[-1].time != self.duration:
            raise ValueError('Samples must span exactly 0..duration')
        return self


class EmissionDeadlines(Contract):
    """Original absolute authority; never renewed by queue or launch delays."""
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, frozen=True)
    fixture_posting_cutoff_mach_seconds: Number = Field(gt=0)
    assignment_expires_unix: Number = Field(gt=0)

    @field_validator('fixture_posting_cutoff_mach_seconds', 'assignment_expires_unix')
    @classmethod
    def finite_double(cls, value):
        try:
            finite = math.isfinite(float(value))
        except OverflowError:
            finite = False
        if not finite:
            raise ValueError('Deadline must be representable as a finite Double')
        return value


class Recipe(Contract):
    schema_version: Literal['bmcp-pen/v1'] = Field(alias='schema')
    session: str = Field(min_length=1, max_length=128)
    expected_binding: str = Field(pattern=r'^[0-9a-f]{64}$')
    target_pid: StrictInt = Field(gt=0)
    bounds: tuple[Number, Number, Number, Number]
    stroke: Stroke
    emission_deadlines: EmissionDeadlines | None = None

    @model_validator(mode='after')
    def containment(self):
        x, y, w, h = self.bounds
        if abs(x) > 100000 or abs(y) > 100000 or not 0 < w <= 4096 or not 0 < h <= 4096:
            raise ValueError('Invalid safety rectangle')
        for p in self.stroke.samples:
            if abs(p.x) > 100000 or abs(p.y) > 100000 or not (x <= p.x < x+w and y <= p.y < y+h):
                raise ValueError('Sample outside safety rectangle')
        return self


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def recipe_hash(recipe):
    return hashlib.sha256(canonical(recipe).encode()).hexdigest()


def preview(payload):
    recipe = Recipe.model_validate(payload).model_dump(mode='json', by_alias=True, exclude_none=True)
    return {'schema': 'bmcp-preview/v1', 'recipe': recipe, 'recipe_hash': recipe_hash(recipe),
            'contacts': 1, 'coordinate_frame': FRAME,
            'native_validation': 'UNKNOWN: displays, foreground, permissions checked by emitter',
            'samples': len(recipe['stroke']['samples'])}


class QuartzBackend:
    def __init__(self, executable: str, sha256: str):
        self.executable = Path(executable)
        self.sha256 = sha256

    def validate(self):
        if sys.platform != 'darwin':
            raise RuntimeError('QuartzPen requires macOS')
        if not self.executable.is_absolute() or not self.executable.is_file():
            raise ValueError('Configure an existing absolute QuartzPen executable')
        if hashlib.sha256(self.executable.read_bytes()).hexdigest() != self.sha256:
            raise ValueError('Configured executable SHA256 mismatch')
        if not os.access(self.executable, os.X_OK):
            raise ValueError('Configured QuartzPen is not executable')

    def run(self, recipe, directory, cancel):
        if recipe.get('emission_deadlines') is not None:
            raise ValueError('Bound recipes require the v2 application backend')
        self.validate()
        if cancel.is_set():
            return {'quiescent': True, 'outcome': 'cancelled', 'release': 'not started', 'release_attempted': True}
        directory = Path(directory)
        path = directory / 'stroke.json'
        path.write_text(canonical(recipe['stroke']))
        path.chmod(0o600)
        argv = [str(self.executable), '--input', str(path), '--bounds',
                *map(str, recipe['bounds']), '--emit', '--target-pid', str(recipe['target_pid'])]
        # Files avoid pipe backpressure during release; no shell or mutable helper imports.
        with (directory / 'emitter.jsonl').open('xb') as out, (directory / 'emitter.stderr').open('xb') as err:
            process = subprocess.Popen(argv, stdout=out, stderr=err, start_new_session=True)
            # Custody begins at Popen, before any fallible metadata persistence.
            started = time.monotonic()
            error = None
            try:
                (directory / 'process.json').write_text(canonical({'pid': process.pid,
                    'executable': str(self.executable), 'sha256': self.sha256}))
            except Exception as exc:
                error = str(exc)
            signalled = None
            while True:
                try:
                    if process.poll() is not None:
                        break
                except Exception as exc:
                    error = error or str(exc)
                if (error or cancel.is_set() or time.monotonic() - started > 20) and signalled is None:
                    signalled = time.monotonic()
                    try:
                        process.terminate()  # Existing handler attempts up + proximity exit.
                    except Exception as exc:
                        error = error or str(exc)
                if signalled is not None and time.monotonic() - signalled > 5:
                    return {'quiescent': False, 'outcome': 'UNKNOWN', 'release': 'UNKNOWN',
                            'error': error, 'reason': 'Exit unverified after SIGTERM; lease retained',
                            'pid': process.pid}
                time.sleep(0.02)
        evidence = emitter_evidence(directory / 'emitter.jsonl', recipe)
        return {'quiescent': True, 'exit_code': process.returncode, 'error': error,
                'outcome': 'UNKNOWN' if error else 'cancelled' if cancel.is_set() else 'completed' if process.returncode == 0 else 'UNKNOWN',
                **evidence}


def emitter_evidence(path, recipe):
    """Report emitter observations only. Never promotes logs to native receipt."""
    result = {'posted': 'UNKNOWN', 'received': 'UNKNOWN', 'applied': 'UNKNOWN',
              'form_achieved': 'UNKNOWN', 'release': 'UNKNOWN', 'release_attempted': False}
    try:
        if path.stat().st_size > 2_000_000:
            return result
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        posts = [r for r in rows if r.get('post_called') is True]
        result['post_calls_observed'] = len(posts)
        # Full log available for independent analysis; v1 deliberately makes no
        # coverage PASS from record counts or process exit.
        if posts:
            result['posted'] = 'observed; coverage unverified'
        up = any(r.get('kind') == 'contact_end' and r.get('cg_pressure') == 0
                 and r.get('mouse_pressure') == 0 and r.get('buttons') == 0 for r in posts)
        exited = any(r.get('kind') == 'proximity_exit' and r.get('enter') == 0 for r in posts)
        if up and exited:
            result['release_attempted'] = True
            result['release'] = 'emitter up/exit observed; native release UNKNOWN'
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return result
