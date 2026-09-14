import os

import pytest

from core import env, git
from core import settings as settings_mod
from test_config_keys import two_routes  # noqa: F401
from test_core_git import _enable_backup, _run, bare_remote  # noqa: F401

HDR = {'X-CSRF-Token': 'testtoken', 'X-Requested-With': 'fetch'}


@pytest.fixture
def pushed(bare_remote, two_routes, monkeypatch):  # noqa: F811
    monkeypatch.setattr(git, '_GIT_ALLOWED_SCHEMES', git._GIT_ALLOWED_SCHEMES + ('file://',))
    monkeypatch.setattr(settings_mod, '_get_static_config_path', lambda: '')
    before = settings_mod.load_settings()
    _enable_backup('file://' + bare_remote)
    ok, err = git._git_push_configs('keys')
    assert ok, err
    yield two_routes
    settings_mod.save_settings(
        domains=before['domains'], cert_resolver=before['cert_resolver'],
        traefik_api_url=before['traefik_api_url'], auth_enabled=before['auth_enabled'],
        password_hash=before['password_hash'], visible_tabs=before['visible_tabs'],
        git_backup_enabled=False, git_backup_repo='')


def _repo_commit(files):
    repo = git._git_repo_dir()
    for name, body in files.items():
        path = os.path.join(repo, *name.split('/'))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as fh:
            fh.write(body)
    _run('git', '-C', repo, 'add', '-A')
    _run('git', '-C', repo, 'commit', '-q', '-m', 'legacy')
    return _run('git', '-C', repo, 'rev-parse', 'HEAD').stdout.strip()


def test_git_restore_writes_each_file_from_its_own_folder(client, pushed):
    a, b = pushed
    sha = _run('git', '-C', git._git_repo_dir(), 'rev-parse', 'HEAD').stdout.strip()
    a.write_text('http: {}\n')
    b.write_text('http: {}\n')
    r = client.post(f'/api/backup/git/restore/{sha}', headers=HDR)
    assert r.status_code == 200, r.get_data(as_text=True)
    assert 'one: {}' in a.read_text() and 'two: {}' in b.read_text(), \
        'one commit blob was spread over both same-named files'


def test_git_restore_skips_a_flat_blob_that_matches_two_files(client, pushed):
    a, b = pushed
    _run('git', '-C', git._git_repo_dir(), 'rm', '-q', '-r', 'dynamic/one', 'dynamic/two')
    sha = _repo_commit({'dynamic/routes.yml': 'http:\n  routers:\n    legacy: {}\n'})
    for f in (a, b):
        f.write_text('http:\n  routers:\n    current: {}\n')
    r = client.post(f'/api/backup/git/restore/{sha}', headers=HDR)
    assert r.status_code == 200, r.get_data(as_text=True)
    assert 'legacy' not in a.read_text() and 'legacy' not in b.read_text()


def _backup(name, body):
    os.makedirs(env.BACKUP_DIR, exist_ok=True)
    with open(os.path.join(env.BACKUP_DIR, name), 'w') as fh:
        fh.write(body)


def test_a_folder_backup_restores_into_its_own_file(client, app_module, two_routes, monkeypatch):  # noqa: F811
    monkeypatch.setattr(app_module, 'BACKUP_DIR', env.BACKUP_DIR)
    a, b = two_routes
    _backup('two__routes.yml.20260101_000000.bak', 'http:\n  routers:\n    restored: {}\n')
    r = client.post('/api/restore/two__routes.yml.20260101_000000.bak', headers=HDR)
    assert r.status_code == 200, r.get_data(as_text=True)
    assert 'restored' in b.read_text() and 'restored' not in a.read_text()


def test_an_old_backup_matching_two_files_is_refused(client, app_module, two_routes, monkeypatch):  # noqa: F811
    monkeypatch.setattr(app_module, 'BACKUP_DIR', env.BACKUP_DIR)
    a, b = two_routes
    _backup('routes.yml.20260101_000000.bak', 'http:\n  routers:\n    stale: {}\n')
    r = client.post('/api/restore/routes.yml.20260101_000000.bak', headers=HDR)
    assert r.status_code == 409, r.get_data(as_text=True)
    assert 'stale' not in a.read_text() and 'stale' not in b.read_text()


def test_an_old_backup_with_a_unique_name_still_restores(client):
    name = os.path.basename(env.CONFIG_PATH) + '.20260101_000000.bak'
    _backup(name, 'http:\n  routers:\n    marker: {}\n')
    r = client.post('/api/restore/' + name, headers=HDR)
    assert r.status_code == 200, r.get_data(as_text=True)
    assert 'marker' in open(env.CONFIG_PATH).read()
