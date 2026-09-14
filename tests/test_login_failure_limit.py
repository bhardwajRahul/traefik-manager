import time

import pyotp
import pytest

import core.settings as settings_mod
from core import auth as auth_mod
from core import env as env_mod

PASSWORD = 'Right-pass-1234'


@pytest.fixture
def account(app_module):
    before = settings_mod.load_settings()
    settings_mod.update_settings(password_hash=app_module._hash_password(PASSWORD), otp_enabled=False,
                                 otp_secret='', must_change_password=False)
    yield
    settings_mod.update_settings(password_hash=before['password_hash'], otp_enabled=before.get('otp_enabled', False),
                                 otp_secret=before.get('otp_secret', ''))


@pytest.fixture
def two_factor(account):
    secret = pyotp.random_base32()
    settings_mod.update_settings(otp_enabled=True, otp_secret=secret)
    return secret


def _client(app_module, ip):
    c = app_module.app.test_client()
    c.environ_base['REMOTE_ADDR'] = ip
    c.get('/login')
    return c


def _token(c):
    with c.session_transaction() as s:
        return s['csrf_token']


def _login(c, password):
    return c.post('/login', data={'csrf_token': _token(c), 'password': password})


def _code(c, code):
    c.get('/login/otp')
    return c.post('/login/otp', data={'csrf_token': _token(c), 'code': code})


def _wrong(secret):
    right = pyotp.TOTP(secret)
    now = time.time()
    taken = {right.at(now + step * 30) for step in (-1, 0, 1)}
    return next(c for c in ('000000', '111111', '222222', '333333') if c not in taken)


def test_failures_spread_over_many_addresses_hit_the_account_limit(app_module, account, monkeypatch):
    monkeypatch.setattr(env_mod, 'LOGIN_FAILURE_LIMIT', '3 per minute')
    codes = [_login(_client(app_module, f'203.0.113.{i}'), 'wrong-password').status_code for i in range(4)]
    assert codes == [200, 200, 200, 429], codes
    assert _login(_client(app_module, '203.0.113.50'), PASSWORD).status_code == 429


def test_successful_sign_ins_do_not_count(app_module, account, monkeypatch):
    monkeypatch.setattr(env_mod, 'LOGIN_FAILURE_LIMIT', '2 per minute')
    codes = [_login(_client(app_module, f'203.0.113.{i}'), PASSWORD).status_code for i in range(4)]
    assert codes == [302] * 4, codes


def test_an_empty_limit_turns_the_account_limit_off(app_module, account, monkeypatch):
    monkeypatch.setattr(env_mod, 'LOGIN_FAILURE_LIMIT', '')
    codes = [_login(_client(app_module, f'203.0.113.{i}'), 'wrong-password').status_code for i in range(40)]
    assert 429 not in codes


def test_failed_codes_spread_over_many_addresses_hit_the_account_limit(app_module, two_factor, monkeypatch):
    monkeypatch.setattr(env_mod, 'OTP_FAILURE_LIMIT', '2 per minute')
    codes = []
    for i in range(3):
        c = _client(app_module, f'198.51.100.{i}')
        assert _login(c, PASSWORD).status_code == 302
        codes.append(_code(c, _wrong(two_factor)).status_code)
    assert codes == [200, 200, 429], codes


def test_a_password_step_allows_only_five_wrong_codes(app_module, two_factor):
    c = _client(app_module, '198.51.100.7')
    _login(c, PASSWORD)
    with c.session_transaction() as s:
        pending = dict(s)
    codes = [_code(c, _wrong(two_factor)).status_code for _ in range(5)]
    assert codes == [200, 200, 200, 200, 302], codes

    replay = _client(app_module, '198.51.100.8')
    with replay.session_transaction() as s:
        s.clear()
        s.update(pending, csrf_token='replayed')
    r = replay.post('/login/otp', data={'csrf_token': 'replayed', 'code': pyotp.TOTP(two_factor).now()})
    assert r.status_code == 302 and r.headers['Location'].endswith('/login'), \
        'replaying the cookie from the password step gave the attacker more guesses'
    assert replay.get('/api/auth/otp/status').status_code != 200


def test_a_password_step_expires(app_module, two_factor):
    c = _client(app_module, '198.51.100.9')
    _login(c, PASSWORD)
    with c.session_transaction() as s:
        s['otp_started'] -= auth_mod.OTP_PENDING_SECONDS + 1
        s['csrf_token'] = 'kept'
    r = c.post('/login/otp', data={'csrf_token': 'kept', 'code': pyotp.TOTP(two_factor).now()})
    assert r.status_code == 302 and r.headers['Location'].endswith('/login')
    assert c.get('/api/auth/otp/status').status_code != 200


def test_the_right_code_still_signs_in(app_module, two_factor):
    c = _client(app_module, '198.51.100.10')
    _login(c, PASSWORD)
    _code(c, _wrong(two_factor))
    r = _code(c, pyotp.TOTP(two_factor).now())
    assert r.status_code == 302 and '/login' not in r.headers['Location']
    assert c.get('/api/auth/otp/status').status_code == 200


def test_failure_limit_parsing(monkeypatch):
    monkeypatch.delenv('LOGIN_FAILURE_LIMIT', raising=False)
    assert env_mod.failure_limit('LOGIN_FAILURE_LIMIT', 'd') == 'd'
    for off in ('', ' ', '0', 'off'):
        monkeypatch.setenv('LOGIN_FAILURE_LIMIT', off)
        assert env_mod.failure_limit('LOGIN_FAILURE_LIMIT', 'd') == ''
    monkeypatch.setenv('LOGIN_FAILURE_LIMIT', 'lots')
    assert env_mod.failure_limit('LOGIN_FAILURE_LIMIT', 'd') == 'd'
    monkeypatch.setenv('LOGIN_FAILURE_LIMIT', '50 per minute;500 per day')
    assert env_mod.failure_limit('LOGIN_FAILURE_LIMIT', 'd') == '50 per minute;500 per day'
