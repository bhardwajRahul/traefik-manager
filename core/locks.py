import hashlib
import os
import threading
from contextlib import contextmanager

try:
    import fcntl
except ImportError:
    fcntl = None

_guard = threading.Lock()
_locks = {}


def _thread_lock(path):
    key = os.path.abspath(str(path))
    with _guard:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _locks[key] = lock
        return lock


@contextmanager
def file_lock(path):
    with _thread_lock(path):
        fh = None
        if fcntl is not None:
            try:
                fh = open(str(path) + '.lock', 'a+')
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            except Exception:
                if fh is not None:
                    try:
                        fh.close()
                    except Exception:
                        pass
                fh = None
        try:
            yield
        finally:
            if fh is not None:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass
                try:
                    fh.close()
                except Exception:
                    pass


_held = threading.local()


@contextmanager
def config_edit_lock(scope):
    from core import env
    key  = str(scope or 'config:local')
    held = getattr(_held, 'scopes', None)
    if held is None:
        held = _held.scopes = set()
    if key in held:
        yield
        return
    base = os.path.join(os.path.dirname(os.path.abspath(env.SETTINGS_PATH)), '.locks')
    try:
        os.makedirs(base, exist_ok=True)
    except OSError:
        pass
    with file_lock(os.path.join(base, hashlib.sha1(key.encode()).hexdigest())):
        held.add(key)
        try:
            yield
        finally:
            held.discard(key)
