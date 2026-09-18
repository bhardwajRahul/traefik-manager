import re

import pytest

import core.settings as settings_mod


def _reset_state(client):
    s = settings_mod.load_settings()
    settings_mod.save_settings(
        domains=s['domains'], cert_resolver=s['cert_resolver'],
        traefik_api_url=s['traefik_api_url'], auth_enabled=True,
        password_hash=s['password_hash'], visible_tabs=s['visible_tabs'],
        setup_complete=True, setup_password_reset=True,
        must_change_password=True,
    )


def test_reset_mode_shows_only_the_password_form(client):
    _reset_state(client)
    r = client.get('/setup')
    body = r.get_data(as_text=True)
    assert r.status_code == 200
    assert 'Set a new password' in body
    assert 'id="panel-0"' not in body, 'the wizard panels must not render in reset mode'
    assert 'id="wzb-0"' not in body, 'the wizard step sidebar must not render in reset mode'
    assert 'Choose your views' not in body


def test_reset_sets_the_password_and_clears_the_flag(client):
    _reset_state(client)
    r = client.post('/setup', data={'csrf_token': 'testtoken',
                                    'password': 'a-brand-new-password',
                                    'confirm': 'a-brand-new-password'})
    assert r.status_code == 302
    s = settings_mod.load_settings()
    assert s['setup_password_reset'] is False
    assert s['must_change_password'] is False
    assert s['setup_complete'] is True, 'reset must not disturb setup_complete'


def test_reset_rejects_a_short_password(client):
    _reset_state(client)
    r = client.post('/setup', data={'csrf_token': 'testtoken',
                                    'password': 'short', 'confirm': 'short'})
    assert 'at least 8 characters' in r.get_data(as_text=True)
    assert settings_mod.load_settings()['setup_password_reset'] is True


def test_reset_rejects_a_mismatch(client):
    _reset_state(client)
    r = client.post('/setup', data={'csrf_token': 'testtoken',
                                    'password': 'a-brand-new-password',
                                    'confirm': 'something-else-entirely'})
    assert 'do not match' in r.get_data(as_text=True)
    assert settings_mod.load_settings()['setup_password_reset'] is True


def test_setup_is_untouched_when_the_flag_is_off(client):
    s = settings_mod.load_settings()
    settings_mod.save_settings(
        domains=s['domains'], cert_resolver=s['cert_resolver'],
        traefik_api_url=s['traefik_api_url'], auth_enabled=True,
        password_hash=s['password_hash'], visible_tabs=s['visible_tabs'],
        setup_complete=True, setup_password_reset=False,
        must_change_password=False,
    )
    r = client.get('/setup')
    assert r.status_code == 302, 'completed setup should still redirect away'


def _tok(c, path):
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', c.get(path).get_data(as_text=True))
    return m.group(1) if m else ''


@pytest.fixture
def restore_reset():
    before = settings_mod.load_settings()
    yield
    settings_mod.update_settings(
        password_hash=before['password_hash'], otp_secret=before.get('otp_secret', ''),
        otp_enabled=before.get('otp_enabled', False), auth_enabled=before.get('auth_enabled', True),
        oidc_enabled=before.get('oidc_enabled', False), setup_complete=before.get('setup_complete', True),
        setup_password_reset=False, must_change_password=False, session_epoch=0)


def _took_over(anon_client, before_hash):
    return (settings_mod.load_settings()['password_hash'] != before_hash
            or anon_client.get('/api/auth/otp/status').status_code == 200)


def test_reset_asks_for_the_two_factor_code(anon_client, restore_reset):
    import pyotp
    _reset_state(None)
    secret = pyotp.random_base32()
    settings_mod.update_settings(otp_secret=secret, otp_enabled=True)
    before = settings_mod.load_settings()['password_hash']
    assert 'name="code"' in anon_client.get('/setup').get_data(as_text=True), \
        'the reset page must ask for the authenticator code when two-factor is on'
    token = _tok(anon_client, '/setup')
    for code in ('', '000000'):
        anon_client.post('/setup', data={'csrf_token': token, 'password': 'Taken-over-1',
                                         'confirm': 'Taken-over-1', 'code': code})
        assert not _took_over(anon_client, before), 'the password was reset without a valid two-factor code'
    r = anon_client.post('/setup', data={'csrf_token': token, 'password': 'Recovered-pass-1',
                                         'confirm': 'Recovered-pass-1', 'code': pyotp.TOTP(secret).now()})
    assert r.status_code == 302 and settings_mod.load_settings()['setup_password_reset'] is False


def test_reset_is_refused_when_admin_password_is_set(anon_client, monkeypatch, restore_reset):
    _reset_state(None)
    monkeypatch.setenv('ADMIN_PASSWORD', 'env-owns-the-password')
    before = settings_mod.load_settings()['password_hash']
    anon_client.post('/setup', data={'csrf_token': _tok(anon_client, '/setup'),
                                     'password': 'Attacker-pass-1', 'confirm': 'Attacker-pass-1'})
    assert not _took_over(anon_client, before), \
        'the reset handed out a session although ADMIN_PASSWORD means its password is never used'
    assert settings_mod.load_settings()['setup_password_reset'] is False


def test_reset_is_refused_when_local_login_is_off(anon_client, restore_reset):
    _reset_state(None)
    settings_mod.update_settings(auth_enabled=False, oidc_enabled=True)
    before = settings_mod.load_settings()['password_hash']
    anon_client.post('/setup', data={'csrf_token': _tok(anon_client, '/setup'),
                                     'password': 'Attacker-pass-1', 'confirm': 'Attacker-pass-1'})
    assert not _took_over(anon_client, before), 'the reset let someone past the identity provider'
    assert settings_mod.load_settings()['setup_password_reset'] is False


def test_a_two_factor_sign_in_closes_the_reset_window(anon_client, app_module, restore_reset):
    import pyotp
    secret = pyotp.random_base32()
    settings_mod.update_settings(password_hash=app_module._hash_password('Temp-pass-1234'), otp_secret=secret,
                                 otp_enabled=True, setup_password_reset=True, must_change_password=False,
                                 setup_complete=True)
    anon_client.post('/login', data={'csrf_token': _tok(anon_client, '/login'), 'password': 'Temp-pass-1234'})
    anon_client.post('/login/otp', data={'csrf_token': _tok(anon_client, '/login/otp'),
                                         'code': pyotp.TOTP(secret).now()})
    assert anon_client.get('/api/auth/otp/status').status_code == 200
    assert settings_mod.load_settings()['setup_password_reset'] is False, \
        'a two-factor sign-in left the reset window open'


def test_setup_posts_are_rate_limited(anon_client, restore_reset):
    _reset_state(None)
    token = _tok(anon_client, '/setup')
    codes = [anon_client.post('/setup', data={'csrf_token': token, 'password': 'x', 'confirm': 'y'}).status_code
             for _ in range(6)]
    assert codes[-1] == 429, codes
