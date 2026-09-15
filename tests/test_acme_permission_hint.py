import json
import os

import pytest


@pytest.mark.skipif(os.geteuid() == 0, reason='root reads a mode 000 file')
def test_an_unreadable_store_names_the_owner_not_chmod(client, tmp_path, monkeypatch):
    path = tmp_path / 'acme.json'
    path.write_text(json.dumps({'le': {'Certificates': []}}))
    path.chmod(0o000)
    monkeypatch.setenv('ACME_JSON_PATH', str(path))
    from core import env as env_mod
    env_mod.set_settings_paths('acme', str(path))
    try:
        body = client.get('/api/traefik/certs').get_json()
    finally:
        path.chmod(0o600)
    assert f'Permission denied reading {path}' in body['error']
    assert 'user that owns it' in body['error']
    assert 'chmod o+r' not in body['error'], 'Traefik refuses an acme.json with any other permission'
