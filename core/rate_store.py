import json
import os
import threading
import time

from limits.storage import MemoryStorage, Storage

from core import env
from core.env import logger
from core.locks import file_lock

SCHEME = 'tmshared://'
MAX_KEYS = 10000


class SharedStorage(Storage):
    STORAGE_SCHEME = ['tmshared']

    def __init__(self, uri=None, wrap_exceptions=False, **options):
        super().__init__(uri, wrap_exceptions=wrap_exceptions, **options)
        self._memory = MemoryStorage()
        self._failed = False

    @property
    def base_exceptions(self):
        return OSError

    def _path(self):
        return os.path.join(env.CONFIG_DIR, '.rate_limits.json')

    def _read(self):
        try:
            with open(self._path(), encoding='utf-8') as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return {}
        except ValueError:
            return {}
        if not isinstance(data, dict):
            return {}
        now = time.time()
        out = {}
        for key, value in data.items():
            try:
                count, expires = int(value[0]), float(value[1])
            except (TypeError, ValueError, IndexError, KeyError):
                continue
            if expires > now:
                out[str(key)] = [count, expires]
        return out

    def _write(self, data):
        if len(data) > MAX_KEYS:
            data = dict(sorted(data.items(), key=lambda kv: (kv[1][0], kv[1][1]))[-MAX_KEYS:])
        path = self._path()
        tmp  = f"{path}.tmp.{os.getpid()}.{threading.get_ident()}"
        try:
            with open(tmp, 'w', encoding='utf-8') as fh:
                json.dump(data, fh, separators=(',', ':'))
            os.replace(tmp, path)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _shared(self, op, fallback, locked=True):
        try:
            if locked:
                with file_lock(self._path()):
                    result = op()
            else:
                result = op()
        except OSError as e:
            if not self._failed:
                self._failed = True
                logger.warning(f"Rate limits are counted per worker until {self._path()} is writable: {e}")
            return fallback()
        if self._failed:
            self._failed = False
            logger.info("Rate limits are shared between workers again")
        return result

    def incr(self, key, expiry, amount=1):
        def op():
            data = self._read()
            count, expires = data.get(key, [0, time.time() + expiry])
            data[key] = [count + amount, expires]
            self._write(data)
            return count + amount
        return self._shared(op, lambda: self._memory.incr(key, expiry, amount))

    def get(self, key):
        if self._failed:
            return self._memory.get(key)
        return self._shared(lambda: self._read().get(key, [0, 0])[0],
                            lambda: self._memory.get(key), locked=False)

    def get_expiry(self, key):
        if self._failed:
            return self._memory.get_expiry(key)
        return self._shared(lambda: self._read().get(key, [0, time.time()])[1],
                            lambda: self._memory.get_expiry(key), locked=False)

    def check(self):
        return True

    def clear(self, key):
        self.clear_prefix(key, exact=True)

    def clear_prefix(self, prefix, exact=False):
        def op():
            data = self._read()
            keep = {k: v for k, v in data.items()
                    if not (k == prefix if exact else k.startswith(prefix))}
            if len(keep) != len(data):
                self._write(keep)
        for key in list(self._memory.storage):
            if key == prefix if exact else key.startswith(prefix):
                self._memory.clear(key)
        self._shared(op, lambda: None)

    def reset(self):
        def op():
            count = len(self._read())
            self._write({})
            return count
        self._memory.reset()
        return self._shared(op, lambda: 0)
