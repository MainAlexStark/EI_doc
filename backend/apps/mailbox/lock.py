"""Замок «один ящик — одна синхронизация» с токеном владельца и продлением.

В проде кеш — общий Redis (settings.CACHES), поэтому замок работает между
процессами web, worker и фоновым потоком кнопки «Проверить почту». Снимает
только владелец (токен), а пока синхронизация идёт — продлевает срок.
Упал процесс — замок сам истечёт через TTL.
"""

from __future__ import annotations

import secrets
from contextlib import contextmanager

from django.core.cache import cache

TTL = 180


class Busy(Exception):
    pass


class BoxLock:
    def __init__(self, box: str):
        self.key = f"mailbox:sync:{box}"
        self.token = secrets.token_hex(16)

    def acquire(self) -> bool:
        return bool(cache.add(self.key, self.token, timeout=TTL))

    def renew(self) -> bool:
        if cache.get(self.key) != self.token:
            return False
        cache.touch(self.key, TTL)
        return True

    def release(self) -> None:
        if cache.get(self.key) == self.token:
            cache.delete(self.key)


@contextmanager
def box_lock(box: str):
    lock = BoxLock(box)
    if not lock.acquire():
        raise Busy(box)
    try:
        yield lock
    finally:
        lock.release()
