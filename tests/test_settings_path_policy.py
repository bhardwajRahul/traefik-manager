import os

import pytest

import core.settings as settings_mod
from conftest import post_json
from core import config as config_mod
from core import env as env_mod

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(config_mod.__file__)))
HDR = {'X-CSRF-Token': 'testtoken', 'X-Requested-With': 'fetch'}


@pytest.fixture(autouse=True)
def _restore_paths():
    before = settings_mod.load_settings()
    yield
    settings_mod.update_settings(static_config_path=before.get('static_config_path', ''),
                                 access_log_path=before.get('access_log_path', ''),
                                 acme_json_path=before.get('acme_json_path', ''))
    settings_mod._get_static_config_path()
    settings_mod._get_access_log_path()
    settings_mod._get_acme_json_path()


def _base(**over):
    s = settings_mod.load_settings()
    body = {'domains': ','.join(s['domains']), 'cert_resolver': s['cert_resolver'],
            'traefik_api_url': s['traefik_api_url']}
    body.update(over)
    return body


@pytest.mark.parametrize('kind', ['static', 'log', 'acme'])
def test_kernel_files_own_state_and_code_are_refused(kind):
    for path in ('/proc/self/environ', '/sys/kernel/notes', '/dev/mem',
                 env_mod.SECRET_KEY_PATH, env_mod.OTP_KEY_PATH, env_mod.SETTINGS_PATH, env_mod.AGENTS_PATH,
                 os.path.join(env_mod.BACKUP_DIR, 'dynamic.yml.bak'),
                 os.path.join(APP_DIR, 'gunicorn.conf.py'), os.path.join(APP_DIR, 'core', 'names.py'),
                 os.path.join(APP_DIR, 'templates', 'login.html')):
        assert config_mod.settings_path_problem(kind, path), f'{kind} accepted {path}'


def test_file_type_rules(tmp_path):
    static = tmp_path / 'traefik.yml'
    static.write_text('api: {}\n')
    script = tmp_path / 'run.py'
    script.write_text('')
    notes = tmp_path / 'notes.txt'
    notes.write_text('')
    certs = tmp_path / 'letsencrypt'
    certs.mkdir()
    logs = tmp_path / 'logs'
    logs.mkdir()

    problem = config_mod.settings_path_problem
    assert problem('static', str(static)) == ''
    assert problem('static', str(tmp_path / 'traefik.toml')), 'a static path must be an existing file'
    assert problem('static', str(script))
    assert problem('acme', str(certs)) == ''
    assert problem('acme', f"{certs / 'ovh.json'}, {certs / 'lan.json'}") == ''
    assert problem('acme', str(notes))
    assert problem('acme', f"{certs / 'ovh.json'}, {notes}")
    assert problem('log', str(logs / 'access.log')) == ''
    assert problem('log', str(logs)), 'a log path must not open a whole directory'
    assert problem('acme', os.path.join(APP_DIR, 'acme.json')) == '', 'data files beside the app stay allowed'
    assert problem('log', os.path.join(APP_DIR, 'logs', 'access.log')) == ''


def test_saving_a_refused_path_is_rejected(client):
    before = settings_mod.load_settings()['static_config_path']
    r = post_json(client, '/api/settings', _base(static_config_path=os.path.join(APP_DIR, 'gunicorn.conf.py')))
    assert r.status_code == 400 and 'Static config path' in r.get_json()['error'], r.get_data(as_text=True)
    assert settings_mod.load_settings()['static_config_path'] == before


def test_saving_an_allowed_path_still_works(client, tmp_path):
    log = tmp_path / 'access.log'
    log.write_text('')
    r = post_json(client, '/api/settings', _base(access_log_path=str(log)))
    assert r.status_code == 200, r.get_data(as_text=True)
    assert settings_mod.load_settings()['access_log_path'] == str(log)


def test_a_refused_value_already_in_manager_yml_is_not_read(client):
    settings_mod.update_settings(access_log_path='/proc/self/environ')
    body = client.get('/api/traefik/logs').get_json()
    assert body['lines'] == [] and 'refused' in body['error'], body
    assert not config_mod.readable_config_path('/proc/self/environ')


def test_the_static_editor_does_not_write_a_refused_path(client):
    target = os.path.join(APP_DIR, 'gunicorn.conf.py')
    with open(target) as f:
        original = f.read()
    settings_mod.update_settings(static_config_path=target)
    r = client.post('/api/static/config', json={'content': 'api: {}\n'}, headers=HDR)
    assert r.status_code == 400 and 'refused' in r.get_json()['error'], r.get_data(as_text=True)
    with open(target) as f:
        assert f.read() == original
    assert 'refused' in client.get('/api/static/config').get_json()['error']


def test_changing_a_settings_path_unregisters_the_old_one(tmp_path):
    a = tmp_path / 'a' / 'access.log'
    b = tmp_path / 'b' / 'access.log'
    for f in (a, b):
        f.parent.mkdir()
        f.write_text('')
    settings_mod.update_settings(access_log_path=str(a))
    settings_mod._get_access_log_path()
    assert config_mod.readable_config_path(str(a))
    settings_mod.update_settings(access_log_path=str(b))
    settings_mod._get_access_log_path()
    assert config_mod.readable_config_path(str(b))
    assert not config_mod.readable_config_path(str(a)), 'the old access log path stayed readable'


def test_a_value_matching_the_environment_is_the_operators_choice(monkeypatch):
    target = os.path.join(APP_DIR, 'gunicorn.conf.py')
    monkeypatch.setenv('ACCESS_LOG_PATH', target)
    settings_mod.update_settings(access_log_path=target)
    assert settings_mod._get_access_log_path() == target
    assert settings_mod.refused_path('log') == ''
