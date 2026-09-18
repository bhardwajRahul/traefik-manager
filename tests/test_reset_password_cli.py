import bcrypt
import pytest

import core.settings as settings_mod
from conftest import SETTINGS_PATH

OLD_PASSWORD = 'the-old-password'
NEW_PASSWORD = 'a-brand-new-password'
OTP_SECRET = 'JBSWY3DPEHPK3PXP'


def _set_state(app_module, **kw):
    s = settings_mod.load_settings()
    fields = dict(
        domains=s['domains'],
        cert_resolver=s['cert_resolver'],
        traefik_api_url=s['traefik_api_url'],
        auth_enabled=True,
        password_hash=app_module._hash_password(OLD_PASSWORD),
        visible_tabs=s['visible_tabs'],
        must_change_password=False,
        setup_password_reset=False,
        setup_complete=True,
        otp_secret='',
        otp_enabled=False,
    )
    fields.update(kw)
    settings_mod.save_settings(**fields)


@pytest.fixture
def runner(app_module):
    _set_state(app_module, otp_secret=OTP_SECRET, otp_enabled=True)
    yield app_module.app.test_cli_runner()
    _set_state(app_module)


def _run(runner, *args, **kw):
    return runner.invoke(args=['reset-password'] + list(args), **kw)


def _raw():
    from ruamel.yaml import YAML
    yaml = YAML()
    with open(SETTINGS_PATH) as f:
        return yaml.load(f) or {}


def _stderr(result):
    try:
        return result.stderr
    except ValueError:
        return result.output


def test_no_arguments_keeps_the_temporary_password_behaviour(runner):
    before = _raw()['password_hash']
    result = _run(runner)
    assert result.exit_code == 0, result.output
    raw = _raw()
    assert raw['password_hash'] != before
    assert raw['must_change_password'] is True
    assert raw['setup_password_reset'] is True
    lines = result.output.splitlines()
    assert lines[0] == '=' * 60
    assert lines[1] == 'TRAEFIK MANAGER - PASSWORD RESET'
    assert lines[2].startswith('New temporary password: ')
    assert lines[3] == 'You will be required to change it on next login.'
    assert lines[4] == '=' * 60
    assert len(lines) == 5


def test_no_arguments_prints_a_password_that_actually_works(runner):
    result = _run(runner)
    assert result.exit_code == 0, result.output
    line = [ln for ln in result.output.splitlines()
            if ln.startswith('New temporary password: ')][0]
    password = line.split(': ', 1)[1]
    assert bcrypt.checkpw(password.encode(), _raw()['password_hash'].encode())


def test_stdin_sets_an_explicit_password(runner):
    result = _run(runner, '--stdin', input=NEW_PASSWORD + '\n')
    assert result.exit_code == 0, result.output
    raw = _raw()
    assert bcrypt.checkpw(NEW_PASSWORD.encode(), raw['password_hash'].encode())
    assert raw['must_change_password'] is False
    assert raw.get('setup_password_reset', False) is False
    assert raw['setup_complete'] is True
    assert NEW_PASSWORD not in result.output


def test_prompt_asks_twice_and_sets_the_password(runner):
    result = _run(runner, '--prompt', input=NEW_PASSWORD + '\n' + NEW_PASSWORD + '\n')
    assert result.exit_code == 0, result.output
    raw = _raw()
    assert bcrypt.checkpw(NEW_PASSWORD.encode(), raw['password_hash'].encode())
    assert raw['must_change_password'] is False
    assert raw.get('setup_password_reset', False) is False
    assert raw['setup_complete'] is True
    assert NEW_PASSWORD not in result.output


def test_prompt_rejects_a_mismatched_confirmation(runner):
    before = _manager_bytes()
    result = _run(runner, '--prompt', input=NEW_PASSWORD + '\nsomething-else\n')
    assert result.exit_code != 0
    assert 'do not match' in _stderr(result) + result.output
    assert _manager_bytes() == before


def test_password_option_sets_the_password_and_warns_on_stderr(runner):
    result = _run(runner, '--password', NEW_PASSWORD)
    assert result.exit_code == 0, result.output
    raw = _raw()
    assert bcrypt.checkpw(NEW_PASSWORD.encode(), raw['password_hash'].encode())
    assert raw['must_change_password'] is False
    assert raw.get('setup_password_reset', False) is False
    assert raw['setup_complete'] is True
    err = _stderr(result)
    assert 'ps output and shell history' in err
    assert '--stdin' in err


def test_explicit_paths_never_echo_the_password(runner):
    result = _run(runner, '--password', NEW_PASSWORD)
    assert NEW_PASSWORD not in result.output
    assert NEW_PASSWORD not in _stderr(result)


def test_disable_otp_on_the_random_path(runner):
    result = _run(runner, '--disable-otp')
    assert result.exit_code == 0, result.output
    raw = _raw()
    assert raw['otp_secret'] == ''
    assert raw['otp_enabled'] is False
    assert raw['must_change_password'] is True
    assert raw['setup_password_reset'] is True


def test_disable_otp_on_the_explicit_path(runner):
    result = _run(runner, '--disable-otp', '--stdin', input=NEW_PASSWORD + '\n')
    assert result.exit_code == 0, result.output
    raw = _raw()
    assert raw['otp_secret'] == ''
    assert raw['otp_enabled'] is False
    assert raw['must_change_password'] is False
    assert raw.get('setup_password_reset', False) is False
    assert bcrypt.checkpw(NEW_PASSWORD.encode(), raw['password_hash'].encode())


def test_otp_is_preserved_when_the_flag_is_absent(runner):
    result = _run(runner, '--stdin', input=NEW_PASSWORD + '\n')
    assert result.exit_code == 0, result.output
    assert settings_mod.load_settings()['otp_secret'] == OTP_SECRET
    assert _raw()['otp_enabled'] is True


@pytest.mark.parametrize('args,stdin', [
    (['--password', 'short'], None),
    (['--stdin'], 'short\n'),
    (['--prompt'], 'short\nshort\n'),
])
def test_a_short_password_is_rejected_and_nothing_is_written(runner, args, stdin):
    before = open(SETTINGS_PATH).read()
    result = _run(runner, *args, input=stdin)
    assert result.exit_code != 0
    assert 'at least 8 characters' in _stderr(result)
    assert open(SETTINGS_PATH).read() == before


def test_exactly_eight_characters_is_accepted(runner):
    result = _run(runner, '--stdin', input='eightchr\n')
    assert result.exit_code == 0, result.output
    assert bcrypt.checkpw(b'eightchr', _raw()['password_hash'].encode())


def test_seven_characters_is_rejected_and_nothing_is_written(runner):
    before = open(SETTINGS_PATH).read()
    result = _run(runner, '--stdin', input='sevench\n')
    assert result.exit_code != 0
    assert 'at least 8 characters' in _stderr(result)
    assert open(SETTINGS_PATH).read() == before


@pytest.mark.parametrize('args,stdin', [
    (['--password', ''], None),
    (['--stdin'], '\n'),
    (['--prompt'], '\n\n'),
])
def test_an_empty_password_is_rejected_and_nothing_is_written(runner, args, stdin):
    before = open(SETTINGS_PATH).read()
    result = _run(runner, *args, input=stdin)
    assert result.exit_code != 0
    assert 'must not be empty' in _stderr(result)
    assert open(SETTINGS_PATH).read() == before


@pytest.mark.parametrize('args,stdin', [
    (['--stdin', '--prompt'], NEW_PASSWORD + '\n'),
    (['--stdin', '--password', NEW_PASSWORD], NEW_PASSWORD + '\n'),
    (['--prompt', '--password', NEW_PASSWORD], NEW_PASSWORD + '\n' + NEW_PASSWORD + '\n'),
])
def test_two_input_options_together_are_an_error(runner, args, stdin):
    before = open(SETTINGS_PATH).read()
    result = _run(runner, *args, input=stdin)
    assert result.exit_code != 0
    assert 'Use only one of' in _stderr(result)
    assert open(SETTINGS_PATH).read() == before


def test_an_explicit_password_closes_a_pending_setup_reset(runner):
    first = _run(runner)
    assert first.exit_code == 0, first.output
    assert _raw()['setup_password_reset'] is True

    second = _run(runner, '--stdin', input=NEW_PASSWORD + '\n')
    assert second.exit_code == 0, second.output
    raw = _raw()
    assert raw['setup_password_reset'] is False, \
        'omitting the argument would leave /setup open to anyone who can reach the app'
    assert raw['must_change_password'] is False
    assert raw['setup_complete'] is True
    assert bcrypt.checkpw(NEW_PASSWORD.encode(), raw['password_hash'].encode())


def _manager_bytes():
    with open(SETTINGS_PATH, 'rb') as f:
        return f.read()


def test_a_password_over_the_bcrypt_limit_is_rejected_cleanly(runner):
    before = _manager_bytes()
    result = _run(runner, '--password', 'a' * 73)
    assert result.exit_code != 0
    assert 'Traceback' not in (result.output or '')
    assert '72 bytes' in _stderr(result) + result.output
    assert _manager_bytes() == before


def test_the_limit_counts_bytes_not_characters(runner):
    before = _manager_bytes()
    result = _run(runner, '--password', '\u00e9' * 40)
    assert result.exit_code != 0, '40 accented characters are 80 bytes'
    assert '72 bytes' in _stderr(result) + result.output
    assert _manager_bytes() == before


def test_exactly_seventy_two_bytes_is_accepted(runner):
    result = _run(runner, '--password', 'a' * 72)
    assert result.exit_code == 0, result.output
    assert bcrypt.checkpw(b'a' * 72, _raw()['password_hash'].encode())


def test_stdin_takes_the_first_line_only(runner):
    result = _run(runner, '--stdin', input='goodpassword\nrubbish-second-line\n')
    assert result.exit_code == 0, result.output
    assert bcrypt.checkpw(b'goodpassword', _raw()['password_hash'].encode())


def test_a_byte_order_mark_on_stdin_is_not_part_of_the_password(runner):
    result = _run(runner, '--stdin', input=b'\xef\xbb\xbf' + NEW_PASSWORD.encode() + b'\n')
    assert result.exit_code == 0, result.output
    assert bcrypt.checkpw(NEW_PASSWORD.encode(), _raw()['password_hash'].encode())


def test_non_utf8_on_stdin_is_rejected_cleanly(runner):
    before = _manager_bytes()
    result = _run(runner, '--stdin', input=b'pass\xffword\n')
    assert result.exit_code != 0
    assert 'not valid UTF-8' in _stderr(result) + result.output
    assert _manager_bytes() == before


def test_admin_password_env_blocks_a_misleading_success(runner, monkeypatch):
    monkeypatch.setenv('ADMIN_PASSWORD', 'set-in-the-environment')
    before = _manager_bytes()
    result = _run(runner, '--password', 'chosenpassword')
    assert result.exit_code != 0, 'it would report success while login ignored the hash'
    assert 'ADMIN_PASSWORD' in _stderr(result) + result.output
    assert _manager_bytes() == before


def test_all_three_input_options_together_is_an_error(runner):
    before = _manager_bytes()
    result = _run(runner, '--prompt', '--stdin', '--password', 'somepassword')
    assert result.exit_code != 0
    assert _manager_bytes() == before


def test_setup_complete_false_is_preserved(runner, app_module):
    _set_state(app_module, setup_complete=False, otp_secret=OTP_SECRET, otp_enabled=True)
    result = _run(runner, '--password', 'chosenpassword')
    assert result.exit_code == 0, result.output
    assert _raw()['setup_complete'] is False


def test_stdin_returns_without_waiting_for_the_stream_to_close(runner):
    import os
    import subprocess
    import sys
    from conftest import SETTINGS_PATH

    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ)
    env.update({'FLASK_APP': 'app.py', 'SETTINGS_PATH': str(SETTINGS_PATH),
                'PYTHONPATH': repo})
    proc = subprocess.Popen(
        [sys.executable, '-m', 'flask', 'reset-password', '--stdin'],
        cwd=repo, env=env, stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
    try:
        proc.stdin.write('a-brand-new-password\n')
        proc.stdin.flush()
        try:
            proc.wait(timeout=25)
        except subprocess.TimeoutExpired:
            raise AssertionError(
                'reset-password --stdin waited for the stream to close instead of '
                'the password line, so any caller that inherits a terminal on '
                'stdin hangs with no prompt and no output')
        assert proc.returncode == 0
    finally:
        if proc.poll() is None:
            proc.kill()
        try:
            proc.stdin.close()
        except Exception:
            pass


def test_a_reset_signs_out_every_session(runner):
    before = settings_mod.load_settings().get('session_epoch', 0)
    assert _run(runner).exit_code == 0
    assert settings_mod.load_settings()['session_epoch'] == before + 1
    assert _run(runner, '--disable-otp').exit_code == 0
    assert settings_mod.load_settings()['session_epoch'] == before + 2
