import os

from core import backups, env

HDR = {'X-CSRF-Token': 'testtoken', 'X-Requested-With': 'fetch'}


def _stamps(*values):
    seq = list(values)
    return lambda: seq.pop(0) if len(seq) > 1 else seq[0]


def test_a_backup_from_the_same_second_restores_its_own_content(client, app_module, tmp_path, monkeypatch):
    monkeypatch.setattr(env, 'BACKUP_DIR', str(tmp_path / 'backups'))
    monkeypatch.setattr(app_module, 'BACKUP_DIR', str(tmp_path / 'backups'))
    monkeypatch.setattr(backups, '_stamp', _stamps('20260101_000000', '20260101_000000', '20260101_000001'))
    monkeypatch.setattr(backups, '_pause', lambda: None)
    with open(env.CONFIG_PATH, 'w') as fh:
        fh.write('http:\n  routers:\n    original: {}\n')
    name = os.path.basename(backups.create_backup(env.CONFIG_PATH))
    with open(env.CONFIG_PATH, 'w') as fh:
        fh.write('http:\n  routers:\n    changed: {}\n')
    r = client.post('/api/restore/' + name, headers=HDR)
    assert r.status_code == 200, r.get_data(as_text=True)
    with open(env.CONFIG_PATH) as fh:
        assert 'original' in fh.read(), \
            'the safety backup taken before the restore overwrote the backup being restored'


def test_a_backup_never_overwrites_an_earlier_one(tmp_path, monkeypatch):
    cfg = tmp_path / 'dynamic.yml'
    monkeypatch.setattr(env, 'CONFIG_PATHS', [str(cfg)])
    monkeypatch.setattr(env, 'ACTIVE_CONFIG_DIR', '')
    monkeypatch.setattr(env, 'BACKUP_DIR', str(tmp_path / 'backups'))
    monkeypatch.setattr(backups, '_stamp', _stamps('20260101_000000', '20260101_000000', '20260101_000001'))
    monkeypatch.setattr(backups, '_pause', lambda: None)
    cfg.write_text('first\n')
    first = backups.create_backup(str(cfg))
    cfg.write_text('second\n')
    second = backups.create_backup(str(cfg))
    assert first != second
    with open(first) as fh:
        assert fh.read() == 'first\n'
