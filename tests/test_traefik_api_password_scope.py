import pytest

import core.settings as settings_mod
from conftest import post_json
from test_git_token_scope import _Sink

HDR = {'X-CSRF-Token': 'testtoken', 'X-Requested-With': 'fetch'}
SAVED = 'http://traefik.invalid:8080'


@pytest.fixture(autouse=True)
def _restore_traefik_settings():
    before = settings_mod.load_settings()
    yield
    settings_mod.update_settings(traefik_api_url=before['traefik_api_url'],
                                 traefik_api_user=before.get('traefik_api_user', ''),
                                 traefik_api_password=before.get('traefik_api_password', ''))


def _store(url, user='admin', password='stored-secret'):
    settings_mod.update_settings(traefik_api_url=url, traefik_api_user=user, traefik_api_password=password)


def _base(**over):
    s = settings_mod.load_settings()
    body = {'domains': ','.join(s['domains']), 'cert_resolver': s['cert_resolver'],
            'traefik_api_url': s['traefik_api_url']}
    body.update(over)
    return body


def test_the_connection_test_does_not_send_the_saved_password_elsewhere(client):
    sink = _Sink()
    try:
        _store(SAVED)
        r = client.post('/api/settings/test-connection', json={'url': sink.url}, headers=HDR)
        assert sink.seen == [], 'the saved Traefik API password was sent to a URL the caller picked'
        assert 'only sent to the saved API URL' in (r.get_json() or {}).get('error', '')
    finally:
        sink.close()


def test_the_connection_test_still_uses_the_saved_password_for_the_saved_url(client):
    sink = _Sink()
    try:
        _store(sink.url)
        client.post('/api/settings/test-connection', json={'url': sink.url + '/'}, headers=HDR)
        assert sink.seen, 'testing the saved URL must still authenticate'
    finally:
        sink.close()


def test_saving_a_new_host_without_the_password_is_refused(client):
    _store(SAVED)
    r = post_json(client, '/api/settings', _base(traefik_api_url='http://other-host.invalid:8080',
                                                 traefik_api_user='admin', traefik_api_password=''))
    assert r.status_code == 400 and 'Re-enter the Traefik API password' in r.get_json()['error'], \
        'the next poll would have sent the stored password to the new host'
    assert settings_mod.load_settings()['traefik_api_url'] == SAVED


def test_saving_a_new_host_with_the_password_is_accepted(client):
    _store(SAVED)
    r = post_json(client, '/api/settings', _base(traefik_api_url='http://other-host.invalid:8080',
                                                 traefik_api_user='admin', traefik_api_password='new-secret'))
    assert r.status_code == 200, r.get_data(as_text=True)
    saved = settings_mod.load_settings()
    assert saved['traefik_api_url'] == 'http://other-host.invalid:8080'
    assert saved['traefik_api_password'] == 'new-secret'


def test_a_trailing_slash_or_case_change_keeps_the_saved_password(client):
    _store(SAVED)
    r = post_json(client, '/api/settings', _base(traefik_api_url='http://TRAEFIK.invalid:8080/',
                                                 traefik_api_user='admin', traefik_api_password=''))
    assert r.status_code == 200, r.get_data(as_text=True)
    assert settings_mod.load_settings()['traefik_api_password'] == 'stored-secret'


def test_without_a_saved_password_a_new_url_saves_normally(client):
    _store(SAVED, password='')
    r = post_json(client, '/api/settings', _base(traefik_api_url='http://other-host.invalid:8080',
                                                 traefik_api_user='admin', traefik_api_password=''))
    assert r.status_code == 200, r.get_data(as_text=True)


@pytest.mark.parametrize('a,b,same', [
    ('http://traefik:8080', 'http://traefik:8080/', True),
    ('http://Traefik:8080', 'http://traefik:8080', True),
    ('https://traefik', 'https://traefik:443', True),
    ('http://traefik', 'http://traefik:80', True),
    ('http://traefik:8080', 'http://traefik:8081', False),
    ('http://traefik:8080', 'https://traefik:8080', False),
    ('http://traefik:8080', 'http://evil:8080', False),
    ('http://traefik:8080/api', 'http://traefik:8080', False),
    ('', 'http://traefik:8080', False),
    ('http://traefik:notaport', 'http://traefik', False),
])
def test_same_api_origin(a, b, same):
    from core.config import same_api_origin
    assert same_api_origin(a, b) is same
