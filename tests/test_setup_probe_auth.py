import pytest

import core.settings as settings_mod

HDR = {'X-CSRF-Token': 'testtoken', 'X-Requested-With': 'fetch'}


@pytest.fixture
def first_run():
    before = settings_mod.load_settings()
    settings_mod.update_settings(setup_complete=False, auth_enabled=True,
                                 password_hash=before['password_hash'] or '$2b$12$' + 'x' * 53)
    yield
    settings_mod.update_settings(setup_complete=before['setup_complete'], auth_enabled=before['auth_enabled'],
                                 password_hash=before['password_hash'])


def _stranger(app_module):
    c = app_module.app.test_client()
    with c.session_transaction() as s:
        s['csrf_token'] = 'testtoken'
    return c


def _no_outbound(monkeypatch, app_module):
    calls = []

    def git(*a, **k):
        calls.append(('git', a, k))
        return '', '', 0

    def http_get(*a, **k):
        calls.append(('http', a, k))
        raise AssertionError('the server reached out on behalf of an unauthenticated caller')

    monkeypatch.setattr(app_module, '_git_run', git)
    monkeypatch.setattr(app_module.requests, 'get', http_get)
    return calls


def test_the_setup_checks_answer_nobody_who_is_not_signed_in(app_module, monkeypatch, first_run):
    calls = _no_outbound(monkeypatch, app_module)
    c = _stranger(app_module)
    for path, body in (('/setup/test-git', {'repo_url': 'https://git.example.com/r.git'}),
                       ('/setup/test-crowdsec', {'url': 'http://crowdsec:8080', 'key': 'k'})):
        assert c.post(path, json=body, headers=HDR).status_code == 404, path
    assert calls == []


def test_admin_password_installs_are_covered_too(app_module, monkeypatch, first_run):
    monkeypatch.setenv('ADMIN_PASSWORD', 'env-password-1')
    calls = _no_outbound(monkeypatch, app_module)
    r = _stranger(app_module).post('/setup/test-git', json={'repo_url': 'https://git.example.com/r.git'}, headers=HDR)
    assert r.status_code == 404, \
        'installs using ADMIN_PASSWORD never finish the wizard, so this check stayed open to anyone'
    assert calls == []


def test_a_signed_in_admin_can_still_run_the_checks(client, app_module, monkeypatch, first_run):
    seen = []
    monkeypatch.setattr(app_module, '_ssrf_ok', lambda url: True)

    def git(args, **kw):
        seen.append(kw)
        return '', '', 0

    monkeypatch.setattr(app_module, '_git_run', git)
    r = client.post('/setup/test-git', json={'repo_url': 'https://git.example.com/r.git'}, headers=HDR)
    assert r.status_code == 200 and r.get_json()['ok'] is True
    assert seen and seen[0].get('extra_config') == ['http.followRedirects=false'], \
        'a public URL could redirect git to a metadata address'


def test_the_git_checks_refuse_link_local_targets(client, app_module, monkeypatch, first_run):
    seen = []

    def git(*a, **k):
        seen.append(a)
        return '', '', 0

    monkeypatch.setattr(app_module, '_git_run', git)
    for path in ('/setup/test-git', '/api/backup/git/test'):
        r = client.post(path, json={'repo_url': 'http://169.254.169.254/latest.git'}, headers=HDR)
        assert r.status_code == 400 and 'not allowed' in r.get_json()['error'], path
    assert seen == []


def test_the_saved_repo_check_does_not_follow_redirects(client, app_module, monkeypatch):
    seen = []
    monkeypatch.setattr(app_module, '_ssrf_ok', lambda url: True)

    def git(args, **kw):
        seen.append(kw)
        return '', '', 0

    monkeypatch.setattr(app_module, '_git_run', git)
    client.post('/api/backup/git/test', json={'repo_url': 'https://git.example.com/r.git'}, headers=HDR)
    assert seen and seen[0].get('extra_config') == ['http.followRedirects=false']


def test_extra_git_config_goes_before_the_command(monkeypatch):
    from core import git as git_mod
    captured = {}

    class _Done:
        stdout = ''
        stderr = ''
        returncode = 0

    def fake_run(cmd, **kw):
        captured['cmd'] = cmd
        return _Done()

    monkeypatch.setattr(git_mod.subprocess, 'run', fake_run)
    git_mod._git_run(['ls-remote', '--', 'https://git.example.com/r.git'], cwd='.',
                     extra_config=['http.followRedirects=false'])
    cmd = captured['cmd']
    at = cmd.index('http.followRedirects=false')
    assert cmd[at - 1] == '-c' and at < cmd.index('ls-remote')
