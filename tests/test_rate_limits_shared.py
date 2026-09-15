import os
import subprocess
import sys
import textwrap

import flask

from core import rate_store

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

WORKER = textwrap.dedent("""
    import sys
    from core.rate_store import SharedStorage
    store = SharedStorage()
    for _ in range(int(sys.argv[1])):
        store.incr('login-failures/account', 60)
""")


def test_workers_share_one_count(tmp_path, monkeypatch):
    script = tmp_path / 'worker.py'
    script.write_text(WORKER)
    env = dict(os.environ)
    env.update({
        'PYTHONPATH': REPO,
        'SETTINGS_PATH': str(tmp_path / 'manager.yml'),
        'CONFIG_PATH': str(tmp_path / 'dynamic.yml'),
    })
    procs = [subprocess.Popen([sys.executable, str(script), '40'], env=env, cwd=REPO)
             for _ in range(2)]
    for p in procs:
        assert p.wait(timeout=60) == 0
    monkeypatch.setattr(rate_store.env, 'CONFIG_DIR', str(tmp_path))
    assert rate_store.SharedStorage().get('login-failures/account') == 80, \
        'each worker kept its own count, so two workers doubled every limit'


def test_the_app_limiter_uses_the_shared_store():
    import app as tm
    assert isinstance(tm.limiter.storage, rate_store.SharedStorage)


def test_wrong_codes_count_across_workers(tmp_path, monkeypatch):
    import app as tm
    from core import auth
    monkeypatch.setattr(rate_store.env, 'CONFIG_DIR', str(tmp_path))
    with tm.app.test_request_context():
        auth.start_otp_attempt()
        nonce = flask.session['otp_nonce']
        for _ in range(auth.OTP_MAX_ATTEMPTS - 1):
            auth.record_otp_failure()
        assert not auth.otp_attempt_expired()
        rate_store.SharedStorage().incr(auth._otp_key(nonce), auth.OTP_PENDING_SECONDS)
        assert auth.otp_attempt_expired(), 'a wrong code sent to the other worker must count'
        auth.forget_otp_attempt()
        assert rate_store.SharedStorage().get(auth._otp_key(nonce)) == 0


def test_an_unwritable_config_dir_still_limits(tmp_path, monkeypatch):
    monkeypatch.setattr(rate_store.env, 'CONFIG_DIR', str(tmp_path / 'missing'))
    store = rate_store.SharedStorage()
    assert [store.incr('k', 60) for _ in range(3)] == [1, 2, 3]
    assert store.get('k') == 3, 'a failed write must not reset the count to what the file says'


def test_a_corrupt_file_starts_over(tmp_path, monkeypatch):
    monkeypatch.setattr(rate_store.env, 'CONFIG_DIR', str(tmp_path))
    (tmp_path / '.rate_limits.json').write_text('{not json')
    store = rate_store.SharedStorage()
    assert store.incr('k', 60) == 1
    assert store.get('k') == 1


def test_expired_counts_are_dropped(tmp_path, monkeypatch):
    monkeypatch.setattr(rate_store.env, 'CONFIG_DIR', str(tmp_path))
    store = rate_store.SharedStorage()
    store.incr('old', 60)
    now = rate_store.time.time()
    monkeypatch.setattr(rate_store.time, 'time', lambda: now + 61)
    assert store.get('old') == 0
    assert store.incr('old', 60) == 1
