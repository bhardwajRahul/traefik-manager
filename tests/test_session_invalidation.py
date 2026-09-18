import hashlib
import os
import re
import secrets

import pytest

import core.settings as settings_mod

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HDR = {'X-CSRF-Token': 'testtoken', 'X-Requested-With': 'fetch'}
PROBE = '/api/auth/otp/status'


@pytest.fixture(autouse=True)
def _restore_auth_settings():
    before = settings_mod.load_settings()
    yield
    settings_mod.update_settings(
        password_hash=before['password_hash'], otp_secret=before.get('otp_secret', ''),
        otp_enabled=before.get('otp_enabled', False), api_keys=before.get('api_keys', []),
        must_change_password=False, setup_password_reset=False, session_epoch=0, admin_password_fp='')


def _set_password(app_module, password='Original-pass-1'):
    settings_mod.update_settings(password_hash=app_module._hash_password(password))
    return password


def _signed_in(app_module):
    c = app_module.app.test_client()
    with c.session_transaction() as s:
        s['authenticated'] = True
        s['csrf_token'] = 'testtoken'
        s['epoch'] = settings_mod.load_settings().get('session_epoch', 0)
    return c


def _tok(c, path):
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', c.get(path).get_data(as_text=True))
    return m.group(1) if m else ''


def test_a_session_from_before_the_upgrade_stays_signed_in(client):
    assert settings_mod.load_settings().get('session_epoch') == 0
    assert client.get(PROBE).status_code == 200, 'a cookie with no epoch must keep working after upgrading'


def test_changing_the_password_signs_out_other_sessions_but_not_the_caller(app_module):
    old = _set_password(app_module)
    caller, other = _signed_in(app_module), _signed_in(app_module)
    r = caller.post('/api/auth/change-password', headers=HDR, json={
        'current_password': old, 'new_password': 'Brand-new-pass-2', 'confirm_password': 'Brand-new-pass-2'})
    assert r.status_code == 200, r.get_json()
    assert other.get(PROBE).status_code == 401, 'a stolen cookie survived the password change'
    assert other.get('/').status_code == 302
    assert caller.get(PROBE).status_code == 200, 'the admin who changed it should stay signed in'


def test_a_cli_reset_signs_out_every_session(app_module):
    signed_in = _signed_in(app_module)
    before = settings_mod.load_settings()['session_epoch']
    result = app_module.app.test_cli_runner().invoke(args=['reset-password', '--stdin'], input='Cli-reset-pass-3\n')
    assert result.exit_code == 0, result.output
    assert settings_mod.load_settings()['session_epoch'] == before + 1
    assert signed_in.get(PROBE).status_code == 401


def test_turning_off_two_factor_signs_out_other_sessions(app_module):
    caller, other = _signed_in(app_module), _signed_in(app_module)
    assert caller.post('/api/auth/otp/disable', headers=HDR).status_code == 200
    assert other.get(PROBE).status_code == 401
    assert caller.get(PROBE).status_code == 200


def test_signing_out_other_sessions(app_module, anon_client):
    caller, other = _signed_in(app_module), _signed_in(app_module)
    assert anon_client.post('/api/auth/sessions/revoke', headers=HDR).status_code in (401, 403)
    bad = caller.post('/api/auth/sessions/revoke', headers={'X-CSRF-Token': 'wrong', 'X-Requested-With': 'fetch'})
    assert bad.status_code == 403
    assert other.get(PROBE).status_code == 200, 'a request without a valid CSRF token must not sign anyone out'
    assert caller.post('/api/auth/sessions/revoke', headers=HDR).status_code == 200
    assert other.get(PROBE).status_code == 401
    assert caller.get(PROBE).status_code == 200


def test_a_two_factor_login_left_half_done_cannot_finish_after_a_reset(app_module):
    import pyotp
    password = _set_password(app_module)
    secret = pyotp.random_base32()
    settings_mod.update_settings(otp_secret=secret, otp_enabled=True)
    c = app_module.app.test_client()
    r = c.post('/login', data={'csrf_token': _tok(c, '/login'), 'password': password})
    assert r.status_code == 302 and '/login/otp' in r.headers['Location']
    settings_mod.bump_session_epoch()
    c.post('/login/otp', data={'csrf_token': _tok(c, '/login/otp'), 'code': pyotp.TOTP(secret).now()})
    assert c.get(PROBE).status_code == 401, 'the password changed between the password step and the code'


def test_a_temporary_password_keeps_the_browser_on_the_change_screen(client, app_module):
    key = secrets.token_urlsafe(24)
    settings_mod.update_settings(must_change_password=True, api_keys=[
        {'name': 'phone', 'hash': 'sha256:' + hashlib.sha256(key.encode()).hexdigest(), 'preview': 'x'}])
    page = client.get('/')
    assert page.status_code == 302 and '/force-change-password' in page.headers['Location']
    api = client.get(PROBE)
    assert api.status_code == 403 and api.get_json()['error'] == 'password change required'
    assert client.get('/force-change-password').status_code == 200
    phone = app_module.app.test_client().get(PROBE, headers={'X-Api-Key': key})
    assert phone.status_code == 200, 'the mobile app signs in with an API key and must keep working'


def test_the_setup_wizard_checks_still_work_during_a_first_run(client):
    settings_mod.update_settings(must_change_password=True)
    for endpoint in ('/setup/test-git', '/setup/test-crowdsec'):
        r = client.post(endpoint, headers=HDR, json={})
        assert r.status_code != 403 or 'password change required' not in r.get_data(as_text=True), endpoint


def test_an_admin_password_from_the_environment_is_not_forced_to_change(client, monkeypatch):
    monkeypatch.setenv('ADMIN_PASSWORD', 'from-the-env-1')
    settings_mod.update_settings(must_change_password=True)
    assert client.get(PROBE).status_code == 200


def test_changing_admin_password_between_starts_signs_everyone_out(app_module, monkeypatch):
    monkeypatch.setenv('ADMIN_PASSWORD', 'first-env-password')
    app_module._sync_admin_password_fingerprint()
    stored = settings_mod.load_settings()['admin_password_fp']
    assert settings_mod.load_settings()['session_epoch'] == 0, 'the first start after upgrading must sign nobody out'
    assert stored and 'first-env-password' not in stored
    app_module._sync_admin_password_fingerprint()
    assert settings_mod.load_settings()['session_epoch'] == 0
    monkeypatch.setenv('ADMIN_PASSWORD', 'second-env-password')
    app_module._sync_admin_password_fingerprint()
    assert settings_mod.load_settings()['session_epoch'] == 1


def test_the_epoch_survives_other_saves_and_rejects_garbage():
    settings_mod.update_settings(session_epoch=4)
    settings_mod.update_settings()
    assert settings_mod.load_settings()['session_epoch'] == 4, 'an unrelated save lowered the epoch'
    from core import env as env_mod
    path = env_mod.SETTINGS_PATH
    with open(path, encoding='utf-8') as fh:
        text = fh.read()
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(re.sub(r'(?m)^session_epoch:.*$', 'session_epoch: nope', text))
    assert settings_mod.load_settings()['session_epoch'] == 0


def test_every_sign_in_goes_through_one_session_helper():
    with open(os.path.join(ROOT, 'app.py'), encoding='utf-8') as fh:
        src = fh.read()
    for name in ('def login():', 'def login_otp():', 'def oidc_callback():', 'def setup():'):
        body = src[src.index(name):]
        body = body[:body.index('\n@app.')]
        assert '_start_session(' in body, name + ' builds its own session and skips the reset window and epoch'
    assert "session['authenticated'] = True" not in src


def test_an_oidc_session_is_not_forced_to_change_a_local_password(app_module):
    settings_mod.update_settings(must_change_password=True)
    c = _signed_in(app_module)
    with c.session_transaction() as s:
        s['auth_method'] = 'oidc'
    assert c.get(PROBE).status_code == 200, 'an OIDC user never uses the local password'


def test_login_turned_off_is_not_forced_to_change(client):
    before = settings_mod.load_settings()['auth_enabled']
    try:
        settings_mod.update_settings(must_change_password=True, auth_enabled=False)
        assert client.get(PROBE).status_code == 200, \
            'with login handed to a forward-auth provider there is no local password to change'
    finally:
        settings_mod.update_settings(auth_enabled=before)


def test_a_first_run_goes_through_setup_before_the_password_change(client):
    before = settings_mod.load_settings()['setup_complete']
    try:
        settings_mod.update_settings(must_change_password=True, setup_complete=False)
        page = client.get('/')
        assert page.status_code == 302 and page.headers['Location'].endswith('/setup'), page.headers.get('Location')
    finally:
        settings_mod.update_settings(setup_complete=before)
