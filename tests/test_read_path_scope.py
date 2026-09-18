import os


def _boot(monkeypatch, tmp_path, env_overrides=None):
    import core.config as config_mod
    import core.env as env_mod
    for k in ('ACCESS_LOG_PATH', 'ACME_JSON_PATH', 'STATIC_CONFIG_PATH', 'PLUGINS_DIR'):
        monkeypatch.delenv(k, raising=False)
    for k, v in (env_overrides or {}).items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(env_mod, 'READ_PATHS', [])
    monkeypatch.setattr(env_mod, '_SETTINGS_PATHS', {})
    monkeypatch.setattr(env_mod, '_ENV_STATIC_DIRS', [])
    monkeypatch.setattr(env_mod, 'ALLOWED_FILES', [])
    monkeypatch.setattr(env_mod, 'STATIC_CONFIG_DIRS', [])
    monkeypatch.setattr(env_mod, 'ALLOWED_FILE_PREFIXES', (str(tmp_path / 'cfg') + os.sep,))
    return env_mod, config_mod


def test_a_settings_path_does_not_open_its_whole_directory(monkeypatch, tmp_path):
    env, config = _boot(monkeypatch, tmp_path)
    secrets = tmp_path / 'etc'
    secrets.mkdir()
    (secrets / 'shadow').write_text('root:$6$hash\n')
    (secrets / 'passwd').write_text('root:x:0:0\n')

    env.set_settings_paths('log', str(secrets / 'shadow'))
    assert config.readable_config_path(str(secrets / 'shadow')), \
        'the configured file itself must stay readable, or the Logs tab breaks'
    assert not config.readable_config_path(str(secrets / 'passwd')), \
        'configuring one file must not open every other file beside it'


def test_a_settings_directory_still_opens_that_directory(monkeypatch, tmp_path):
    env, config = _boot(monkeypatch, tmp_path)
    certs = tmp_path / 'letsencrypt'
    certs.mkdir()
    (certs / 'ovh.json').write_text('{}')
    (certs / 'lan.json').write_text('{}')

    env.set_settings_paths('acme', str(certs))
    assert config.readable_config_path(str(certs / 'ovh.json')), \
        'ACME_JSON_PATH may be a directory whose .json files are all read'
    assert config.readable_config_path(str(certs / 'lan.json'))


def test_an_env_path_still_opens_its_directory(monkeypatch, tmp_path):
    logs = tmp_path / 'varlog'
    logs.mkdir()
    (logs / 'access.log').write_text('')
    (logs / 'other.log').write_text('')
    env, config = _boot(monkeypatch, tmp_path,
                        {'ACCESS_LOG_PATH': str(logs / 'access.log')})
    assert config.readable_config_path(str(logs / 'access.log'))
    assert config.readable_config_path(str(logs / 'other.log')), \
        'the operator declared this path in the environment, so its directory stays trusted'


def test_a_settings_static_path_does_not_open_its_directory_for_writing(monkeypatch, tmp_path):
    env, config = _boot(monkeypatch, tmp_path)
    etc = tmp_path / 'etc'
    etc.mkdir()
    (etc / 'traefik.yml').write_text('api: {}\n')

    env.set_settings_paths('static', str(etc / 'traefik.yml'))
    assert config.safe_file_path(str(etc / 'traefik.yml')), \
        'the configured static config must stay writable, or the editor breaks'
    assert not config.safe_file_path(str(etc / 'cron.d')), \
        'configuring one file must not make its whole directory writable'
    assert not config.safe_file_path(str(etc / 'anything-else.yml'))


def test_an_env_static_path_still_opens_its_directory(monkeypatch, tmp_path):
    etc = tmp_path / 'etctraefik'
    etc.mkdir()
    (etc / 'traefik.yml').write_text('api: {}\n')
    env, config = _boot(monkeypatch, tmp_path,
                        {'STATIC_CONFIG_PATH': str(etc / 'traefik.yml')})
    monkeypatch.setattr(env, 'STATIC_CONFIG_DIRS', [str(etc / 'traefik.yml')])
    monkeypatch.setattr(env, 'ALLOWED_FILE_PREFIXES',
                        env.ALLOWED_FILE_PREFIXES + (str(etc) + os.sep,))
    assert config.safe_file_path(str(etc / 'traefik.yml'))
    assert config.safe_file_path(str(etc / 'sibling.yml')), \
        'the operator declared this path in the environment at startup'
