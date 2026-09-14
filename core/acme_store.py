import hashlib
import json
import os
import re
import stat
import time

from core import env, locks
from core.env import logger

SAFE_MODE = 0o600
CERT_KEYS = ('Certificates', 'certificates')
ATTEMPTS = 3


class AcmeStoreError(Exception):
    status = 500


class AcmeStoreReadOnly(AcmeStoreError):
    status = 403


class AcmeStoreChanged(AcmeStoreError):
    status = 409


class AcmeStorePartial(AcmeStoreError):
    status = 500

    def __init__(self, message, removed, backup):
        super().__init__(message)
        self.removed = removed
        self.backup = backup


def _cert_key(resolver_data):
    for key in CERT_KEYS:
        if isinstance(resolver_data.get(key), list):
            return key
    return ''


def _key_part(parent):
    if parent in ('', '.', '/'):
        return 'store'
    return re.sub(r'[^A-Za-z0-9._ -]', '-', parent)


def _qualified_key(path, base):
    return f'{_key_part(os.path.basename(os.path.dirname(path)))}-{base}'


def backup_key(path, all_paths):
    base = os.path.basename(path)
    same = []
    for other in all_paths or []:
        if os.path.basename(other) == base and other not in same:
            same.append(other)
    if len(same) <= 1:
        return base
    key = _qualified_key(path, base)
    if any(other != path and _qualified_key(other, base) == key for other in same):
        return f'{hashlib.sha256(path.encode("utf-8")).hexdigest()[:8]}-{base}'
    return key


def backup_keys(paths):
    return {path: backup_key(path, paths) for path in paths or []}


def store_lock():
    os.makedirs(env.BACKUP_DIR, exist_ok=True)
    return locks.file_lock(os.path.join(env.BACKUP_DIR, '.acme-store'))


def snapshot(path):
    try:
        with open(path, 'rb') as fh:
            return fh.read()
    except OSError as e:
        raise AcmeStoreError(f'Could not read {os.path.basename(path)}: {e}') from e


def _parse(path, raw):
    name = os.path.basename(path)
    try:
        text = raw.decode('utf-8').strip()
    except UnicodeDecodeError as e:
        raise AcmeStoreError(f'{name} is not valid UTF-8, nothing was changed') from e
    if not text:
        return {}
    try:
        data = json.loads(text)
    except ValueError as e:
        raise AcmeStoreError(f'{name} is not valid JSON, nothing was changed: {e}') from e
    if not isinstance(data, dict):
        raise AcmeStoreError(f'{name} does not hold a resolver map, nothing was changed')
    return data


def load(path):
    return _parse(path, snapshot(path))


def writable(path) -> bool:
    if not path or not os.path.isfile(path):
        return False
    return os.access(path, os.W_OK)


def entry_matches(entry, wanted) -> bool:
    domain = entry.get('domain') if isinstance(entry, dict) else None
    if not isinstance(domain, dict):
        return False
    return str(domain.get('main') or '') == str(wanted or '')


def _stamp():
    return time.strftime('%Y%m%d_%H%M%S')


def _wait_for_next_second():
    time.sleep(1.0 - (time.time() % 1.0) + 0.01)


def _write_all(fd, body):
    view = memoryview(body)
    while len(view):
        written = os.write(fd, view)
        if written <= 0:
            raise OSError('the write made no progress')
        view = view[written:]


def backup(path, raw=None, key=None):
    os.makedirs(env.BACKUP_DIR, exist_ok=True)
    if raw is None:
        raw = snapshot(path)
    base = key or os.path.basename(path)
    for _attempt in range(ATTEMPTS + 2):
        dest = os.path.join(env.BACKUP_DIR, f'{base}.{_stamp()}.bak')
        try:
            fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, SAFE_MODE)
        except FileExistsError:
            _wait_for_next_second()
            continue
        try:
            _write_all(fd, raw)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.chmod(dest, SAFE_MODE)
        return dest
    raise AcmeStoreError(f'Could not create a unique backup of {base}, nothing was changed')


def write_in_place(path, data):
    return write_bytes_in_place(path, json.dumps(data, indent=2).encode('utf-8'))


def write_bytes_in_place(path, body, restore=None):
    try:
        before = os.stat(path)
    except OSError:
        before = None
    old_len = before.st_size if before else 0
    padded = body + b' ' * (old_len - len(body)) if old_len > len(body) else body
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT, SAFE_MODE)
        try:
            _write_all(fd, padded)
            os.fsync(fd)
            os.ftruncate(fd, len(body))
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        if restore is not None:
            _put_back(path, restore)
        raise
    mode = stat.S_IMODE(before.st_mode) if before else SAFE_MODE
    if mode & 0o077:
        logger.warning(f"{os.path.basename(path)} had mode {mode:o}, tightening it to 600 so Traefik still reads it")
        mode = SAFE_MODE
    os.chmod(path, mode)
    _verify(path, body, restore)
    return len(body)


def _put_back(path, raw):
    try:
        write_bytes_in_place(path, raw)
    except (OSError, AcmeStoreError):
        logger.exception(f"Could not put {os.path.basename(path)} back after a failed write")


def _is_json(raw):
    try:
        text = raw.decode('utf-8').strip()
        if text:
            json.loads(text)
        return True
    except ValueError:
        return False


def _verify(path, body, restore):
    name = os.path.basename(path)
    try:
        with open(path, 'rb') as fh:
            got = fh.read()
    except OSError as e:
        raise AcmeStoreError(f'Could not read {name} back after writing: {e}') from e
    if got == body:
        return
    if _is_json(got):
        raise AcmeStoreChanged(f'{name} was rewritten by something else while it was being saved. Try again.')
    if restore is not None:
        _put_back(path, restore)
    raise AcmeStoreError(f'{name} did not read back as valid JSON after writing, the previous copy was put back')


def remove(path, wanted, key=None):
    with store_lock():
        return _apply(path, wanted, key=key)


def plan(path, wanted, raw):
    targets = {(str(r or ''), str(m or '')) for r, m in wanted}
    if not targets:
        return 0, None
    data = _parse(path, raw)
    removed = 0
    for resolver_name, resolver_data in data.items():
        if not isinstance(resolver_data, dict):
            continue
        key = _cert_key(resolver_data)
        if not key:
            continue
        kept = []
        for entry in resolver_data[key]:
            main = (entry.get('domain') or {}).get('main') if isinstance(entry, dict) else None
            if (str(resolver_name), str(main or '')) in targets:
                removed += 1
                continue
            kept.append(entry)
        resolver_data[key] = kept
    if not removed:
        return 0, None
    return removed, json.dumps(data, indent=2).encode('utf-8')


def commit(path, raw, body, key=None):
    if snapshot(path) != raw:
        raise AcmeStoreChanged(
            f'{os.path.basename(path)} changed while it was being edited, most likely Traefik renewing a '
            'certificate. Nothing was written, try again.')
    saved = backup(path, raw, key)
    write_bytes_in_place(path, body, restore=raw)
    return saved


def _apply(path, wanted, raw=None, key=None):
    for attempt in range(ATTEMPTS):
        if raw is None:
            raw = snapshot(path)
        removed, body = plan(path, wanted, raw)
        if not removed:
            return 0, None
        try:
            return removed, commit(path, raw, body, key)
        except AcmeStoreChanged:
            if attempt == ATTEMPTS - 1:
                raise
            raw = None
    return 0, None


def remove_many(paths, wanted):
    with store_lock():
        for path in paths:
            if not writable(path):
                raise AcmeStoreReadOnly(f'{os.path.basename(path)} is mounted read only, nothing was changed')
        planned = []
        for path in paths:
            raw = snapshot(path)
            removed, _body = plan(path, wanted, raw)
            if removed:
                planned.append((path, raw))
        keys = backup_keys(paths)
        total, saved = 0, None
        for path, raw in planned:
            try:
                count, backup_path = _apply(path, wanted, raw, keys.get(path))
            except (AcmeStoreError, OSError) as e:
                if total:
                    raise AcmeStorePartial(
                        f'Removed {total} certificate(s), then stopped at {os.path.basename(path)}: {e}',
                        total, saved) from e
                raise
            total += count
            saved = backup_path or saved
        return total, saved
