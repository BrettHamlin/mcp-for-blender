"""Absolute emission authority, separate from application cleanup budgets."""
from __future__ import annotations

import time

from .pen import EmissionDeadlines


class DeadlineExpired(TimeoutError):
    pass


def mach_seconds():
    info = time.get_clock_info('monotonic')
    if (info.implementation != 'mach_absolute_time()' or
            not info.monotonic or info.adjustable):
        raise RuntimeError('Python monotonic clock is not verified mach_absolute_time()')
    return time.monotonic()


class EmissionAuthority:
    def __init__(self, recipe):
        values = recipe.get('emission_deadlines')
        self.deadlines = EmissionDeadlines.model_validate(values) if values is not None else None
        self.expired = None

    def check(self):
        if self.expired:
            raise DeadlineExpired(self.expired)
        if self.deadlines is None:
            return
        if mach_seconds() >= self.deadlines.fixture_posting_cutoff_mach_seconds:
            self.expired = 'fixture posting cutoff expired'
        elif time.time() >= self.deadlines.assignment_expires_unix:
            self.expired = 'original assignment expired'
        if self.expired:
            raise DeadlineExpired(self.expired)
