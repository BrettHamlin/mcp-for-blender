"""Run deadline regressions without package server initialization or real IPC.

Invoke from repository root: .venv/bin/python tests/run_bmcp_deadlines_inert.py
"""
import asyncio
import os
from pathlib import Path
import socket
import ssl  # Import its socket subclass before replacing socket construction.
import sys
import types

import pytest

os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
package = types.ModuleType('blender_mcp')
package.__path__ = [str(Path('src/blender_mcp').resolve())]
sys.modules['blender_mcp'] = package


def forbidden(*args, **kwargs):
    raise AssertionError('Real socket/IPC is forbidden in the inert deadline suite')


socket.socket = socket.socketpair = forbidden
custody = 'tests/test_bmcp_custody.py::'
checks = [
    'tests/test_bmcp_deadlines.py', 'tests/test_bmcp_pen.py', 'tests/test_bmcp_control.py',
    'tests/test_bmcp_revision.py::test_old_reconcile_cannot_release_new_store_lease',
    'tests/test_bmcp_revision.py::test_metadata_failure_supervised_through_coordinator',
    *[custody + name for name in (
        'test_strict_json', 'test_path_guards_and_exclusive_persistence',
        'test_launch_bridge_retains_callback_application_and_requests_new_instance',
        'test_host_loop_stall_cancels_unstarted_launch_operation',
        'test_finished_exact_types_bindings_and_cross_field_rules',
        'test_receipt_verification_refuses_mismatch', 'test_partial_metadata_remains_unknown',
        'test_diagnostic_sink_failure_does_not_lose_callback')],
]
raise SystemExit(pytest.main(['-q', *checks, '-k', 'not mcp_execute']))
