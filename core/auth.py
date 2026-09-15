import os
import secrets
import time
from functools import wraps

from flask import abort, redirect, request, session, url_for

from core import rate_store as rate_store_mod
from core import settings as settings_mod
from core.env import logger

INACTIVITY_TIMEOUT = int(os.environ.get('INACTIVITY_TIMEOUT_MINUTES', '120'))


class _CsrfError(Exception):
    pass


def _auth_enabled() -> bool:
    env = os.environ.get('AUTH_ENABLED', '').strip().lower()
    if env in ('false', '0', 'no'):
        return False
    if env in ('true', '1', 'yes'):
        return True
    return settings_mod.load_settings().get('auth_enabled', True)

def _oidc_active() -> bool:
    return bool(settings_mod.load_settings().get('oidc_enabled'))

def _auth_required() -> bool:
    return _auth_enabled() or _oidc_active()

def _get_csrf_token() -> str:
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_hex(32)
    return session['csrf_token']

def _check_csrf():
    token = request.form.get('csrf_token', '') or request.headers.get('X-CSRF-Token', '')
    if request.is_json:
        token = (request.get_json(silent=True) or {}).get('csrf_token', '') or token
    expected = session.get('csrf_token', '')
    if not expected or not secrets.compare_digest(str(token), str(expected)):
        logger.warning(f"CSRF check failed from {request.remote_addr}")
        if request.headers.get('X-Requested-With') == 'fetch' or request.is_json:
            raise _CsrfError()
        abort(403)

def csrf_protect(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if request.method in ('POST', 'PUT', 'DELETE', 'PATCH'):
            if not _check_api_key():
                _check_csrf()
        return f(*args, **kwargs)
    return decorated

def _check_password(plaintext: str, hashed: str) -> bool:
    import bcrypt
    try:
        return bcrypt.checkpw(plaintext.encode(), hashed.encode())
    except Exception:
        return False

def _verify_api_key(key: str, stored: str) -> bool:
    import hashlib
    if stored.startswith('sha256:'):
        expected = 'sha256:' + hashlib.sha256(key.encode()).hexdigest()
        return secrets.compare_digest(expected, stored)
    return _check_password(key, stored)

def _is_authenticated() -> bool:

    if not _auth_required():
        return True
    return session.get('authenticated') is True

def _session_epoch() -> int:
    try:
        return int(settings_mod.load_settings().get('session_epoch') or 0)
    except (TypeError, ValueError):
        return 0

def _stamp_session():
    session['epoch'] = _session_epoch()

def _drop_stale_session() -> bool:
    if not session.get('authenticated'):
        return False
    if int(session.get('epoch') or 0) == _session_epoch():
        return False
    logger.info(f"Signed out a session issued before the last password change, for {request.remote_addr}")
    session.clear()
    return True

def _check_inactivity():
    if not session.get('authenticated'):
        return
    if _drop_stale_session():
        return
    last = session.get('last_active')
    now  = time.time()
    timeout = INACTIVITY_TIMEOUT * 60 if not session.permanent else INACTIVITY_TIMEOUT * 60 * 24
    if last and (now - last) > timeout:
        logger.info(f"Session expired due to inactivity for {request.remote_addr}")
        session.clear()
        return
    session['last_active'] = now

OTP_MAX_ATTEMPTS = 5
OTP_PENDING_SECONDS = 600
_OTP_PREFIX = 'otp-attempts/'
_otp_store = rate_store_mod.SharedStorage()


def _otp_key(nonce) -> str:
    return _OTP_PREFIX + str(nonce or '')


def start_otp_attempt():
    session['otp_nonce'] = secrets.token_urlsafe(18)
    session['otp_started'] = int(time.time())


def otp_attempt_expired() -> bool:
    started = session.get('otp_started')
    if not session.get('otp_nonce') or not isinstance(started, int):
        return True
    if time.time() - started > OTP_PENDING_SECONDS:
        return True
    return _otp_store.get(_otp_key(session['otp_nonce'])) >= OTP_MAX_ATTEMPTS


def record_otp_failure() -> int:
    return _otp_store.incr(_otp_key(session.get('otp_nonce')), OTP_PENDING_SECONDS)


def forget_otp_attempt():
    nonce = session.get('otp_nonce')
    if nonce:
        _otp_store.clear(_otp_key(nonce))


def reset_otp_attempts():
    _otp_store.clear_prefix(_OTP_PREFIX)


def _check_api_key() -> bool:
    key = request.headers.get('X-Api-Key', '')
    if not key:
        return False
    settings = settings_mod.load_settings()
    api_keys = settings.get('api_keys', [])
    if not api_keys:
        return False
    return any(_verify_api_key(key, k['hash']) for k in api_keys if k.get('hash'))

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if _check_api_key():
            return f(*args, **kwargs)
        _check_inactivity()
        if not _is_authenticated():
            if request.headers.get('X-Api-Key') or request.path.startswith('/api/'):
                abort(401)
            return redirect(url_for('login', next=request.path))
        return f(*args, **kwargs)
    return decorated
