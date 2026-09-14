import os

import pytest

from core import backups, env, git
from core import settings as settings_mod
from test_core_git import _enable_backup, _run, bare_remote  # noqa: F401


def test_unique_names_keep_their_basename(tmp_path, monkeypatch):
    monkeypatch.setattr(env, 'ACTIVE_CONFIG_DIR', str(tmp_path))
    a, b = str(tmp_path / 'a.yml'), str(tmp_path / 'sub' / 'b.yml')
    assert backups.config_keys([a, b]) == {a: 'a.yml', b: 'b.yml'}, \
        'a layout without clashing names must keep today\'s backup and git names'


def test_same_named_files_get_their_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(env, 'ACTIVE_CONFIG_DIR', str(tmp_path))
    a, b, c = (str(tmp_path / 'one' / 'routes.yml'), str(tmp_path / 'two' / 'routes.yml'),
               str(tmp_path / 'solo.yml'))
    keys = backups.config_keys([a, b, c])
    assert keys == {a: 'one/routes.yml', b: 'two/routes.yml', c: 'solo.yml'}
    assert backups.backup_stem(keys[a]) == 'one__routes.yml'


def test_config_paths_in_different_folders_use_their_common_root(tmp_path, monkeypatch):
    monkeypatch.setattr(env, 'ACTIVE_CONFIG_DIR', '')
    a, b = str(tmp_path / 'x' / 'dynamic.yml'), str(tmp_path / 'y' / 'dynamic.yml')
    assert set(backups.config_keys([a, b]).values()) == {'x/dynamic.yml', 'y/dynamic.yml'}


@pytest.fixture
def two_routes(tmp_path, monkeypatch):
    root = tmp_path / 'cfg'
    a, b = root / 'one' / 'routes.yml', root / 'two' / 'routes.yml'
    for f, name in ((a, 'one'), (b, 'two')):
        f.parent.mkdir(parents=True)
        f.write_text(f'http:\n  routers:\n    {name}: {{}}\n')
    monkeypatch.setattr(env, 'ACTIVE_CONFIG_DIR', str(root))
    monkeypatch.setattr(env, 'CONFIG_PATHS', [str(a), str(b)])
    monkeypatch.setattr(env, 'BACKUP_DIR', str(tmp_path / 'backups'))
    return a, b


def test_same_named_files_get_separate_backup_series(two_routes, monkeypatch):
    a, b = two_routes
    monkeypatch.setattr(backups, '_backup_keep_count', lambda: 1)
    first, second = backups.create_backup(str(a)), backups.create_backup(str(b))
    assert os.path.basename(first).startswith('one__routes.yml.')
    assert os.path.basename(second).startswith('two__routes.yml.')
    assert os.path.exists(first) and os.path.exists(second), \
        "pruning one file's backups removed the other file's backup"


def test_a_flat_backup_name_is_unchanged(tmp_path, monkeypatch):
    cfg = tmp_path / 'dynamic.yml'
    cfg.write_text('http: {}\n')
    monkeypatch.setattr(env, 'ACTIVE_CONFIG_DIR', '')
    monkeypatch.setattr(env, 'CONFIG_PATHS', [str(cfg)])
    monkeypatch.setattr(env, 'BACKUP_DIR', str(tmp_path / 'backups'))
    assert os.path.basename(backups.create_backup(str(cfg))).startswith('dynamic.yml.')


def test_push_keeps_same_named_files_apart(bare_remote, two_routes, monkeypatch):  # noqa: F811
    monkeypatch.setattr(git, '_GIT_ALLOWED_SCHEMES', git._GIT_ALLOWED_SCHEMES + ('file://',))
    monkeypatch.setattr(settings_mod, '_get_static_config_path', lambda: '')
    before = settings_mod.load_settings()
    try:
        _enable_backup('file://' + bare_remote)
        ok, err = git._git_push_configs('keys')
        assert ok, err
        files = _run('git', '--git-dir', bare_remote, 'ls-tree', '-r', '--name-only', 'main').stdout.split()
        assert 'dynamic/one/routes.yml' in files and 'dynamic/two/routes.yml' in files, files
        for name in ('one', 'two'):
            blob = _run('git', '--git-dir', bare_remote, 'show', f'main:dynamic/{name}/routes.yml').stdout
            assert f'{name}: {{}}' in blob, f'dynamic/{name}/routes.yml holds the other file'
    finally:
        settings_mod.save_settings(
            domains=before['domains'], cert_resolver=before['cert_resolver'],
            traefik_api_url=before['traefik_api_url'], auth_enabled=before['auth_enabled'],
            password_hash=before['password_hash'], visible_tabs=before['visible_tabs'],
            git_backup_enabled=False, git_backup_repo='')
