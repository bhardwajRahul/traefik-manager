import os

import pytest

from core import agents_store, config, env
from core import settings as st

from conftest import SETTINGS_PATH


@pytest.fixture(autouse=True)
def fresh_cache():
    config.forget_parse()
    yield
    config.forget_parse()


def _raw():
    return SETTINGS_PATH.read_text()


def _set_key(key, value):
    lines = [ln for ln in _raw().splitlines() if not ln.startswith(f'{key}:')]
    lines.append(f'{key}: {value}')
    SETTINGS_PATH.write_text('\n'.join(lines) + '\n')


def test_a_popped_secret_does_not_leak_out_of_the_cache():
    first = st.load_settings()
    assert first['password_hash'], 'the fixture settings must have a hash to lose'
    first.pop('password_hash')
    assert st.load_settings()['password_hash'], \
        'the API response pops the hash off this dict, and the next caller decides whether a password is set'


def test_mutating_what_a_caller_got_back_never_reaches_the_cache():
    first = st.load_settings()
    first['domains'].append('evil.example.com')
    first['visible_tabs']['certs'] = True
    first['session_epoch'] = 999
    second = st.load_settings()
    assert 'evil.example.com' not in second['domains']
    assert second['session_epoch'] != 999
    assert second['visible_tabs'] == st.load_settings()['visible_tabs']


def test_two_callers_never_share_one_dict():
    a = st.load_settings()
    b = st.load_settings()
    assert a is not b
    assert a['visible_tabs'] is not b['visible_tabs']


def test_a_rewrite_the_same_size_at_the_same_mtime_is_still_seen():
    before = st.load_settings()['cert_resolver']
    stat = os.stat(SETTINGS_PATH)
    SETTINGS_PATH.write_text(_raw().replace('cert_resolver: letsencrypt',
                                            'cert_resolver: cloudflareee'[:len('cert_resolver: letsencrypt')]))
    os.utime(SETTINGS_PATH, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert os.stat(SETTINGS_PATH).st_mtime_ns == stat.st_mtime_ns
    assert os.stat(SETTINGS_PATH).st_size == stat.st_size
    after = st.load_settings()['cert_resolver']
    assert after != before, \
        'the cache keys on file content, so a same-size same-mtime write must not be served stale'


def test_a_new_session_epoch_is_seen_at_once():
    st.load_settings()
    _set_key('session_epoch', 42)
    assert st.load_settings()['session_epoch'] == 42, \
        'signing every other session out must take effect on the next request, in every worker'


def test_repeated_calls_parse_the_file_once(monkeypatch):
    st.load_settings()
    calls = []
    real = config.yaml_safe.load
    monkeypatch.setattr(config.yaml_safe, 'load', lambda s: calls.append(1) or real(s))
    for _ in range(5):
        st.load_settings()
    assert calls == [], 'the settings file is parsed on every call again'


def test_a_changed_file_is_parsed_again(monkeypatch):
    st.load_settings()
    calls = []
    real = config.yaml_safe.load
    monkeypatch.setattr(config.yaml_safe, 'load', lambda s: calls.append(1) or real(s))
    _set_key('backup_keep_count', 7)
    assert st.load_settings()['backup_keep_count'] == 7
    assert calls, 'a changed file must be parsed again'


def test_a_missing_file_is_not_remembered(tmp_path, monkeypatch):
    missing = tmp_path / 'manager.yml'
    monkeypatch.setattr(env, 'SETTINGS_PATH', str(missing))
    assert st.load_settings()['setup_complete'] is False
    missing.write_text('setup_complete: true\ndomains:\n  - later.example.com\n')
    assert st.load_settings()['setup_complete'] is True, \
        'a file that appears after the first read must be picked up'


def test_agents_are_cached_and_reread_when_they_change(tmp_path, monkeypatch):
    path = tmp_path / 'agents.yml'
    path.write_text("agents:\n  - id: a1\n    name: one\n    url: https://one.example.com\n")
    monkeypatch.setattr(env, 'AGENTS_PATH', str(path))
    config.forget_parse()
    first = agents_store.load_agents()
    assert [a['name'] for a in first] == ['one']
    first[0]['name'] = 'mutated'
    assert [a['name'] for a in agents_store.load_agents()] == ['one']
    path.write_text("agents:\n  - id: a1\n    name: two\n    url: https://one.example.com\n")
    assert [a['name'] for a in agents_store.load_agents()] == ['two']


def test_an_agent_key_is_not_shared_between_callers(tmp_path, monkeypatch):
    path = tmp_path / 'agents.yml'
    path.write_text("agents:\n  - id: a1\n    name: one\n    url: https://one.example.com\n"
                    "    api_key: plain-secret\n")
    monkeypatch.setattr(env, 'AGENTS_PATH', str(path))
    config.forget_parse()
    a = agents_store.load_agents()
    a[0].pop('api_key', None)
    b = agents_store.load_agents()
    assert b[0].get('api_key'), 'one caller dropping the key must not blind the next one'
    assert a[0] is not b[0]


def test_a_changed_agent_is_seen_through_the_settings(tmp_path, monkeypatch):
    path = tmp_path / 'agents.yml'
    path.write_text("agents:\n  - id: a1\n    name: one\n    url: https://one.example.com\n")
    monkeypatch.setattr(env, 'AGENTS_PATH', str(path))
    config.forget_parse()
    assert [a['name'] for a in st.load_settings()['agents']] == ['one']
    path.write_text("agents:\n  - id: a1\n    name: renamed\n    url: https://one.example.com\n")
    assert [a['name'] for a in st.load_settings()['agents']] == ['renamed'], \
        'settings carry the agent list, so an agents.yml write has to invalidate them too'


def test_a_fresh_read_parses_again(monkeypatch):
    st.load_settings()
    calls = []
    real = config.yaml_safe.load
    monkeypatch.setattr(config.yaml_safe, 'load', lambda s: calls.append(1) or real(s))
    st.load_settings(fresh=True)
    assert calls, 'startup re-encryption needs a real read, it detects plain text while decrypting'
