import ast
import concurrent.futures
import os
import threading

import pytest

from conftest import read_config
from core import agents_store, locks
from core import settings as settings_mod

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as fh:
        return fh.read()


def _save_route(client, name):
    from conftest import post_form
    return post_form(client, '/save', serviceName=name, subdomain=f'{name}.example.com',
                     protocol='http', scheme='http', targetIp='10.0.0.1',
                     targetPort='8080', certResolver='letsencrypt')


def test_the_edit_lock_excludes_and_is_reentrant():
    order = []
    held  = threading.Event()

    def first():
        with locks.config_edit_lock('config:test'):
            with locks.config_edit_lock('config:test'):
                order.append('in')
                held.set()
                threading.Event().wait(0.2)
                order.append('out')

    def second():
        held.wait(2)
        with locks.config_edit_lock('config:test'):
            order.append('second')

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        list(concurrent.futures.as_completed([ex.submit(first), ex.submit(second)]))
    assert order == ['in', 'out', 'second'], order


def test_scopes_do_not_block_each_other():
    done = threading.Event()

    def other():
        with locks.config_edit_lock('config:agent:b'):
            done.set()

    with locks.config_edit_lock('config:agent:a'):
        t = threading.Thread(target=other)
        t.start()
        assert done.wait(2), 'an edit on one server waited for an edit on another'
        t.join()


def test_merging_keeps_what_another_request_changed():
    settings_mod.update_settings(disabled_routes={'a': {'n': 1}, 'b': {'n': 1}})
    before = {'disabled_routes': {'a': {'n': 1}, 'b': {'n': 1}}}
    settings_mod.update_settings(disabled_routes={'a': {'n': 1}, 'b': {'n': 1}, 'other': {'n': 7}})
    settings_mod.merge_settings_dicts(before, disabled_routes={'a': {'n': 2}, 'c': {'n': 3}})
    got = settings_mod.load_settings()['disabled_routes']
    assert got == {'a': {'n': 2}, 'c': {'n': 3}, 'other': {'n': 7}}, got
    settings_mod.update_settings(disabled_routes={})


def test_concurrent_route_saves_into_one_file_all_survive(client):
    names = [f'race{i}' for i in range(6)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(names)) as ex:
        codes = list(ex.map(lambda n: _save_route(client, n).status_code, names))
    assert all(c < 400 for c in codes), codes
    routers = (read_config().get('http') or {}).get('routers') or {}
    missing = [n for n in names if n not in routers]
    assert not missing, f'overlapping saves dropped these routes: {missing}'


def test_config_writers_hold_the_edit_lock():
    tree = ast.parse(_read('app.py'))
    funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    writes = {'save_config', '_agent_write_config', '_save_edit_dicts', '_save_disabled_routes'}
    helpers = {'_toggle_route', '_toggle_route_agent', '_cascade_across_configs', '_save_disabled_routes',
               '_save_edit_dicts'}

    def calls(fn):
        return {getattr(c.func, 'id', None) or getattr(c.func, 'attr', None)
                for c in ast.walk(fn) if isinstance(c, ast.Call)}

    def locked(fn):
        return any(getattr(d, 'id', None) == '_config_edit' for d in fn.decorator_list)

    for name, fn in funcs.items():
        if name in helpers or not (calls(fn) & writes):
            continue
        assert locked(fn), f'{name} edits dynamic config without the edit lock'
    for helper in helpers - {'_save_edit_dicts', '_save_disabled_routes'}:
        for name, fn in funcs.items():
            if helper in calls(fn) and name not in helpers:
                assert locked(fn), f'{name} calls {helper} without the edit lock'


def test_agent_writers_hold_the_agents_lock():
    tree = ast.parse(_read('app.py'))
    for fn in (n for n in tree.body if isinstance(n, ast.FunctionDef)):
        names = {getattr(c.func, 'id', None) or getattr(c.func, 'attr', None)
                 for c in ast.walk(fn) if isinstance(c, ast.Call)}
        if 'save_agents_file' in names and fn.name != '_reencrypt_plaintext_secrets':
            assert any(getattr(d, 'id', None) == '_agents_locked' for d in fn.decorator_list), fn.name


def test_modify_agents_with_no_change_writes_nothing():
    agents_store.save_agents_file([])
    before = os.stat(agents_store.env.AGENTS_PATH).st_mtime_ns
    assert agents_store.modify_agents(lambda agents: False) is False
    assert os.stat(agents_store.env.AGENTS_PATH).st_mtime_ns == before


def test_tab_detection_does_not_revert_a_password_change(monkeypatch):
    from core import monitor
    original = settings_mod.load_settings()
    changed  = '$2b$12$' + 'n' * 53
    threads  = []

    def newly_seen(found, seen):
        t = threading.Thread(target=lambda: settings_mod.update_settings(password_hash=changed))
        t.start()
        threads.append(t)
        threading.Event().wait(0.2)
        return [tab for tab in found if tab not in seen]

    monkeypatch.setattr(monitor.providers_mod, 'newly_seen', newly_seen)
    try:
        settings_mod.update_settings(provider_tabs_seen=[],
                                     visible_tabs=dict(original.get('visible_tabs') or {}, docker=False))
        assert monitor._enable_host_provider_tabs(['docker']) == ['docker']
        threads[0].join(5)
        got = settings_mod.load_settings()
        assert got['password_hash'] == changed, 'turning on a provider tab put the old password back'
        assert got['visible_tabs']['docker'] is True
    finally:
        settings_mod.update_settings(password_hash=original['password_hash'],
                                     visible_tabs=original.get('visible_tabs') or {},
                                     provider_tabs_seen=original.get('provider_tabs_seen') or [])


@pytest.mark.parametrize('module,name', [('monitor', '_enable_host_provider_tabs'),
                                         ('monitor', '_enable_agent_provider_tabs')])
def test_tab_detection_uses_the_locked_helpers(module, name):
    src  = _read('core', f'{module}.py')
    body = src[src.index(f'def {name}('):]
    body = body[:body.index('\ndef ', 10)]
    assert 'modify_settings' in body or 'modify_agents' in body
    assert 'save_settings(' not in body and 'save_agents_file(' not in body
