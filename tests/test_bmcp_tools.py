import asyncio
from types import SimpleNamespace

import pytest
from mcp.server.fastmcp import FastMCP
from blender_mcp.sculpt_tools import register


def test_opt_in_surface_has_nested_versioned_recipe_schema_and_routes_control():
    app = FastMCP('host-only test')
    calls = []
    def send(command, params, **kwargs):
        calls.append((command, params))
        return {'schema': 'bmcp-state/v1'}
    register(app, lambda: SimpleNamespace(host='localhost', send_command=send))
    tools = {t.name: t for t in asyncio.run(app.list_tools())}
    assert set(tools) == {'sculpt_inspect', 'sculpt_bind', 'sculpt_configure', 'sculpt_capture',
        'checkpoint_save', 'checkpoint_restore', 'pen_preview', 'pen_execute', 'pen_status',
        'pen_cancel', 'pen_reconcile', 'pen_record_evidence'}
    recipe = tools['pen_execute'].inputSchema['$defs']['Recipe']
    assert recipe['additionalProperties'] is False
    assert recipe['properties']['schema']['const'] == 'bmcp-pen/v1'
    asyncio.run(app.call_tool('sculpt_inspect', {}))
    assert calls == [('bmcp_control', {'action': 'inspect', 'params': {}})]
