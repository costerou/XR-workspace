"""The live absolute setting reaches the renderer and survives restart; no live services."""
import json
import pytest
from tools.native_spike import view, profiles, ipc


def test_default_light_and_live_persistence(tmp_path):
    path = tmp_path / 'layout.json'
    state = view.ViewState(['XR-1'])
    assert state.view().get('stabilization') == 25
    store = profiles.LayoutStore(path)
    control = view.ViewControl(state, 'run', 'session', None, store, None)
    for strength in (60, 100, 47, 0, 25):
        message = dict(schema=1, request_id=1, run_id='run', session='session', op='stabilization', payload={'strength': strength})
        decoded = ipc.decode_control((json.dumps(message)+'\n').encode())
        frames = control.on_message(decoded)
        assert json.loads(frames)['payload']['stabilization'] == strength
        assert view.load_settings(path)['stabilization'] == strength
        assert profiles._view_part({'stabilization': strength})['stabilization'] == strength
        assert state.settings()['stabilization'] == strength


@pytest.mark.parametrize('bad', [-1, 101, True, 1.5, '25', None])
def test_invalid_strength_is_rejected(bad):
    message = dict(schema=1, request_id=1, run_id='run', session='session', op='stabilization', payload={'strength': bad})
    with pytest.raises(ValueError): ipc.decode_control((json.dumps(message)+'\n').encode())
    with pytest.raises(ValueError): view.ViewState(['XR-1'], stabilization=bad)


def test_legacy_profile_defaults_and_custom_strength_roundtrip(tmp_path):
    path = tmp_path / 'profiles.json'
    path.write_text(json.dumps({'schema': 1, 'profiles': {}}))
    # Existing settings without the key keep working and choose Light.
    assert profiles._view_part({'prediction': False})['stabilization'] == 25
    profiles.save_profile(path, 'beast', {**profiles.DEFAULT_PROFILE, 'stabilization': 47})
    assert profiles.load_profile(path, 'beast')['stabilization'] == 47
    store = profiles.ProfileStore(path, 'beast')
    store.save({**store.load(), 'stabilization': 0})
    assert profiles.load_profile(path, 'beast')['stabilization'] == 0
