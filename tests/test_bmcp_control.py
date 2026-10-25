"""Host boundary tests for the actual embedded addon class, without bpy/GPU."""
import ast
import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import ROOT_ADDON


def load_control(bpy):
    tree = ast.parse(ROOT_ADDON.read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'SculptControl')
    namespace = {'bpy': bpy, 'os': os, 'json': json}
    exec(compile(ast.Module(body=[cls], type_ignores=[]), '<addon>', 'exec'), namespace)
    return namespace['SculptControl']


@pytest.fixture
def setup(tmp_path, monkeypatch):
    bpy = SimpleNamespace(ops=SimpleNamespace(wm=SimpleNamespace()))
    server = SimpleNamespace()
    control = load_control(bpy)(server)
    identity = {k: k for k in ('instance', 'pid', 'file', 'scene_pointer', 'object_pointer',
                               'mesh_pointer', 'window_pointer', 'area_pointer')}
    identity['mode'] = 'SCULPT'
    state = {'identity': identity, 'binding': 'a'*64, 'derived_radius_readbacks': {'brush': .1}}
    control.inspect = lambda: copy.deepcopy(state)
    monkeypatch.setenv('BMCP_ARTIFACT_ROOT', str(tmp_path))
    bound = control.dispatch('bind', {'expected_binding': 'a'*64})
    params = {'session': bound['session'], 'expected_binding': 'a'*64}
    return control, bpy, server, state, params, Path(bound['artifact_directory'])


def test_wrong_instance_and_stale_view_refused(setup):
    control, _, _, state, params, _ = setup
    state['identity']['instance'] = 'another'
    with pytest.raises(ValueError, match='Stale session'):
        control.dispatch('save', {**params, 'name': 'x.blend'})
    state['identity']['instance'] = 'instance'
    state['binding'] = 'b'*64
    with pytest.raises(ValueError, match='Stale state'):
        control.dispatch('capture', {**params, 'name': 'x.png'})


def test_paths_overwrites_capture_errors_and_derived_readbacks(setup):
    control, _, server, state, params, root = setup
    for name in ('../bad.png', '/bad.png', 'bad.blend'):
        with pytest.raises(ValueError):
            control.dispatch('capture', {**params, 'name': name})
    (root / 'exists.png').write_text('preserve')
    with pytest.raises(ValueError, match='overwrite'):
        control.dispatch('capture', {**params, 'name': 'exists.png'})
    server.get_viewport_screenshot = lambda **kw: {'error': 'GPU unavailable'}
    with pytest.raises(RuntimeError, match='GPU unavailable'):
        control.dispatch('capture', {**params, 'name': 'new.png'})
    def capture(**kw):
        Path(kw['filepath']).write_bytes(b'fixture image, not native acceptance')
        state['derived_radius_readbacks']['brush'] = .2
        return {'success': True, 'method': 'offscreen'}
    server.get_viewport_screenshot = capture
    result = control.dispatch('capture', {**params, 'name': 'new.png'})
    assert result['strict_state_preserved']
    assert result['before']['derived_radius_readbacks'] != result['after']['derived_radius_readbacks']
    assert result['capture']['method'] == 'offscreen'


def test_checkpoint_save_restore_integrity_and_explicit_discard(setup):
    control, bpy, _, _, params, root = setup
    bpy.ops.wm.save_as_mainfile = lambda **kw: {'CANCELLED'}
    with pytest.raises(RuntimeError, match='save failed'):
        control.dispatch('save', {**params, 'name': 'copy.blend'})
    def save(**kw):
        assert kw['copy'] is True
        Path(kw['filepath']).write_bytes(b'checkpoint fixture')
        return {'FINISHED'}
    bpy.ops.wm.save_as_mainfile = save
    control.dispatch('save', {**params, 'name': 'copy.blend'})
    with pytest.raises(ValueError, match='discard_current'):
        control.dispatch('restore', {**params, 'name': 'copy.blend', 'discard_current': False})
    (root / 'copy.blend').write_bytes(b'tampered')
    with pytest.raises(ValueError, match='changed'):
        control.dispatch('restore', {**params, 'name': 'copy.blend', 'discard_current': True})
    (root / 'copy.blend').write_bytes(b'checkpoint fixture')
    bpy.ops.wm.open_mainfile = lambda **kw: {'FINISHED'}
    assert control.dispatch('restore', {**params, 'name': 'copy.blend', 'discard_current': True})['sessions_invalidated']
    assert not control.sessions


def test_lease_blocks_mutations_and_wrong_owner_release(setup):
    control, _, _, _, params, _ = setup
    control.dispatch('acquire', {**params, 'request_id': 'one'})
    with pytest.raises(ValueError, match='lease active'):
        control.dispatch('save', {**params, 'name': 'copy.blend'})
    with pytest.raises(ValueError, match='owner mismatch'):
        control.dispatch('release', {'session': params['session'], 'request_id': 'two'})
    control.dispatch('release', {'session': params['session'], 'request_id': 'one'})
    assert control.lease is None


def test_actual_inspection_binds_raw_view_and_separates_radius():
    class RNA(SimpleNamespace):
        def as_pointer(self):
            return id(self)
    matrix = [[1, 0], [0, 1]]
    brush = RNA(name='Draw', size=20, strength=.5, use_locked_size='VIEW', unprojected_radius=.1)
    unified = RNA(size=20, strength=.5, use_locked_size='VIEW', unprojected_radius=.1)
    mesh = RNA(vertices=[RNA(co=[0, 0, 0])], polygons=[])
    obj = RNA(name='Mesh', type='MESH', mode='SCULPT', data=mesh,
              use_dynamic_topology_sculpting=False, matrix_world=matrix)
    region = RNA(type='WINDOW', x=0, y=0, width=640, height=480)
    view = RNA(view_matrix=matrix, window_matrix=matrix)
    area = RNA(type='VIEW_3D', spaces=RNA(active=RNA(region_3d=view)), regions=[region])
    scene = RNA(name='Scene', tool_settings=RNA(sculpt=RNA(brush=brush), unified_paint_settings=unified))
    window = RNA(x=1, y=2, width=640, height=480)
    bpy = RNA(data=RNA(filepath='/disposable.blend'), context=RNA(active_object=obj,
        screen=RNA(areas=[area]), scene=scene, window=window))
    control = load_control(bpy)(None)
    original = control.inspect()
    brush.unprojected_radius = .2
    derived = control.inspect()
    assert derived['binding'] == original['binding']
    assert derived['derived_radius_readbacks'] != original['derived_radius_readbacks']
    brush.use_locked_size = 'SCENE'
    locked = control.inspect()
    brush.unprojected_radius = .3
    assert control.inspect()['binding'] != locked['binding']
    before = control.inspect()
    window.x += 1
    assert control.inspect()['binding'] != before['binding']
    before = control.inspect()
    mesh.vertices[0].co[0] += 1
    assert control.inspect()['binding'] != before['binding']


def test_non_sculpt_contact_and_extra_parameters_refused(setup):
    control, _, _, state, params, _ = setup
    state['identity']['mode'] = 'OBJECT'
    with pytest.raises(ValueError, match='SCULPT'):
        control.dispatch('acquire', {**params, 'request_id': 'one'})
    with pytest.raises(ValueError, match='malformed'):
        control.dispatch('save', {**params, 'name': 'a.blend', 'overwrite': True})


def test_dispatch_enforces_main_thread_and_loopback(setup):
    import threading
    control, _, server, _, _, _ = setup
    errors = []
    def wrong_thread():
        try:
            control.dispatch('inspect', {})
        except RuntimeError as error:
            errors.append(str(error))
    thread = threading.Thread(target=wrong_thread)
    thread.start()
    thread.join()
    assert errors == ['BMCP control requires the Blender main thread']
    server.host = '0.0.0.0'
    with pytest.raises(ValueError, match='loopback'):
        control.dispatch('inspect', {})
