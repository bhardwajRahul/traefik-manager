import os

import pytest

from core import env as env_mod


@pytest.fixture
def config_dir(tmp_path):
    saved = (env_mod.ACTIVE_CONFIG_DIR, env_mod.CONFIG_PATHS, env_mod.CONFIG_PATH,
             env_mod.MULTI_CONFIG, env_mod.ALLOWED_FILE_PREFIXES,
             env_mod._config_scan_at, env_mod._config_scan_mtime)
    directory = tmp_path / 'dynamic'
    directory.mkdir()
    (directory / 'dynamic.yml').write_text('http:\n  routers: {}\n')
    env_mod.ACTIVE_CONFIG_DIR = str(directory)
    env_mod.CONFIG_PATHS = [str(directory / 'dynamic.yml')]
    env_mod.CONFIG_PATH = env_mod.CONFIG_PATHS[0]
    env_mod.MULTI_CONFIG = False
    env_mod._config_scan_at = 0.0
    env_mod._config_scan_mtime = 0
    yield directory
    (env_mod.ACTIVE_CONFIG_DIR, env_mod.CONFIG_PATHS, env_mod.CONFIG_PATH,
     env_mod.MULTI_CONFIG, env_mod.ALLOWED_FILE_PREFIXES,
     env_mod._config_scan_at, env_mod._config_scan_mtime) = saved


def _names():
    return sorted(os.path.basename(p) for p in env_mod.CONFIG_PATHS)


def test_a_file_written_by_another_worker_is_picked_up(config_dir):
    env_mod.refresh_config_paths()
    (config_dir / 'vellum.yml').write_text('http:\n  routers: {}\n')
    env_mod.refresh_config_paths()
    assert _names() == ['dynamic.yml', 'vellum.yml'], \
        'a route saved by the other gunicorn worker vanished from this one'


def test_a_new_file_does_not_wait_for_the_ttl(config_dir, monkeypatch):
    env_mod.refresh_config_paths()
    monkeypatch.setattr(env_mod.time, 'monotonic', lambda: env_mod._config_scan_at)
    (config_dir / 'vellum.yml').write_text('http:\n  routers: {}\n')
    env_mod.refresh_config_paths()
    assert 'vellum.yml' in _names(), \
        'the directory mtime changed, so the rescan must not be held back by the ttl'


def test_an_unchanged_directory_is_not_rescanned(config_dir, monkeypatch):
    env_mod.refresh_config_paths()
    calls = []
    monkeypatch.setattr(env_mod, 'scan_config_dir', lambda *a, **k: calls.append(1) or [])
    env_mod.refresh_config_paths()
    env_mod.refresh_config_paths()
    assert calls == [], 'nothing changed, so every request must not pay for a glob'


def test_the_ttl_still_catches_a_change_in_a_subdirectory(config_dir, monkeypatch):
    env_mod.refresh_config_paths()
    nested = config_dir / 'apps'
    nested.mkdir()
    env_mod.refresh_config_paths()
    (nested / 'vellum.yml').write_text('http:\n  routers: {}\n')
    monkeypatch.setattr(env_mod.time, 'monotonic',
                        lambda: env_mod._config_scan_at + env_mod.CONFIG_SCAN_TTL + 1)
    env_mod.refresh_config_paths()
    assert 'vellum.yml' in _names(), \
        'creating a file in a subdirectory leaves the parent mtime alone, the ttl is the backstop'


def test_a_path_outside_the_directory_survives(config_dir):
    outside = config_dir.parent / 'extra.yml'
    outside.write_text('http:\n  routers: {}\n')
    env_mod.register_config_path(str(outside))
    env_mod.refresh_config_paths(force=True)
    assert 'extra.yml' in _names(), 'a file added by hand from outside the directory was dropped'


def test_a_deleted_file_is_dropped(config_dir):
    (config_dir / 'vellum.yml').write_text('http:\n  routers: {}\n')
    env_mod.refresh_config_paths(force=True)
    (config_dir / 'vellum.yml').unlink()
    env_mod.refresh_config_paths(force=True)
    assert 'vellum.yml' not in _names()


def test_an_empty_directory_keeps_the_default(config_dir):
    (config_dir / 'dynamic.yml').unlink()
    env_mod.CONFIG_PATHS = []
    env_mod.refresh_config_paths(force=True)
    assert _names() == ['dynamic.yml']


def test_single_file_installs_are_untouched(config_dir, monkeypatch):
    monkeypatch.setattr(env_mod, 'ACTIVE_CONFIG_DIR', '')
    before = list(env_mod.CONFIG_PATHS)
    (config_dir / 'vellum.yml').write_text('http:\n  routers: {}\n')
    assert env_mod.refresh_config_paths(force=True) == before


def test_the_routes_tab_refreshes_before_it_reads(config_dir, monkeypatch):
    from core import routes_build

    seen = []
    monkeypatch.setattr(env_mod, 'refresh_config_paths', lambda *a, **k: seen.append(1))
    routes_build._build_all_apps(include_external=False)
    assert seen, 'the routes list must rescan, or the other worker keeps serving a stale file list'
