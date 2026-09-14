import os
import re
import shutil
import time

from core import env
from core import settings as settings_mod
from core.env import logger


def ensure_backup_dir():
    if not os.path.exists(env.BACKUP_DIR):
        os.makedirs(env.BACKUP_DIR)

def _backup_keep_count() -> int:
    try:
        v = settings_mod.load_settings().get('backup_keep_count')
        if v in (None, ''):
            v = os.environ.get('BACKUP_KEEP_COUNT', '0')
        return max(0, int(v))
    except Exception:
        return 0

def _prune_backups(base: str):
    keep = _backup_keep_count()
    if keep <= 0:
        return
    pat = re.compile(r'^' + re.escape(base) + r'\.(\d{8}_\d{6})\.bak$')
    matches = sorted(
        (f for f in os.listdir(env.BACKUP_DIR) if pat.match(f)),
        reverse=True,
    )
    for f in matches[keep:]:
        try:
            os.remove(os.path.join(env.BACKUP_DIR, f))
            logger.info(f"Pruned old backup: {f}")
        except OSError:
            pass

def config_keys(paths=None) -> dict:
    paths  = list(paths if paths is not None else env.CONFIG_PATHS)
    counts = {}
    for p in paths:
        counts[os.path.basename(p)] = counts.get(os.path.basename(p), 0) + 1
    root = ''
    if env.ACTIVE_CONFIG_DIR:
        root = os.path.realpath(env.ACTIVE_CONFIG_DIR)
    elif paths:
        try:
            root = os.path.commonpath([os.path.dirname(os.path.realpath(p)) for p in paths])
        except ValueError:
            root = ''
    keys = {}
    for p in paths:
        base = os.path.basename(p)
        rel  = os.path.relpath(os.path.realpath(p), root) if root and counts[base] > 1 else base
        keys[p] = base if rel.startswith('..') or os.path.isabs(rel) else rel.replace(os.sep, '/')
    return keys


def backup_stem(key: str) -> str:
    return str(key).replace('/', '__')


def backup_base(path) -> str:
    keys = config_keys()
    return backup_stem(keys[path]) if path in keys else os.path.basename(path)


def _stamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def _pause():
    time.sleep(0.05)


def create_backup(path=None):
    if path is None:
        path = env.CONFIG_PATH
    ensure_backup_dir()
    if os.path.exists(path):
        base = backup_base(path)
        dest = os.path.join(env.BACKUP_DIR, f"{base}.{_stamp()}.bak")
        for _ in range(40):
            if not os.path.exists(dest):
                break
            _pause()
            dest = os.path.join(env.BACKUP_DIR, f"{base}.{_stamp()}.bak")
        shutil.copy2(path, dest)
        logger.info(f"Backup created: {dest}")
        _prune_backups(base)
        return dest
    return None
