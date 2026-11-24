"""IMAP-ящик Timeweb: папки, синхронизация по UID, флаги, перенос, APPEND.

Обёртка над imaplib, чтобы остальной код (и тесты с поддельным сервером)
работали с понятными методами, а не с сырыми ответами IMAP.
Имена папок — modified UTF-7 (RFC 3501 §5.1.3): «Отправленные» на сервере
выглядит как «&BB4EQgQ,BEAEMAQyBDsENQQ9BD0ESwQ1-».
"""

from __future__ import annotations

import base64
import functools
import imaplib
import re
import ssl
import time
from dataclasses import dataclass

from apps.mailbox.conf import Box
from apps.mailbox.models import Folder

TIMEOUT = 30


# ---------------------------------------------------------------- modified UTF-7
def utf7_decode(name: str) -> str:
    out, i = [], 0
    while i < len(name):
        ch = name[i]
        if ch != "&":
            out.append(ch)
            i += 1
            continue
        j = name.find("-", i)
        if j == -1:
            out.append(name[i:])
            break
        chunk = name[i + 1:j]
        if not chunk:
            out.append("&")
        else:
            b64 = chunk.replace(",", "/")
            b64 += "=" * (-len(b64) % 4)
            out.append(base64.b64decode(b64).decode("utf-16-be"))
        i = j + 1
    return "".join(out)


def utf7_encode(name: str) -> str:
    out, buf = [], []

    def flush():
        if buf:
            b64 = base64.b64encode("".join(buf).encode("utf-16-be")).decode().rstrip("=").replace("/", ",")
            out.append(f"&{b64}-")
            buf.clear()

    for ch in name:
        if 0x20 <= ord(ch) <= 0x7E:
            flush()
            out.append("&-" if ch == "&" else ch)
        else:
            buf.append(ch)
    flush()
    return "".join(out)


def quote(name: str) -> str:
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


# ---------------------------------------------------------------- роли папок
SPECIAL_USE = {"\\sent": Folder.SENT, "\\junk": Folder.JUNK, "\\trash": Folder.TRASH, "\\archive": Folder.ARCHIVE}
BY_NAME = {
    Folder.SENT: ("sent", "sent messages", "sent items", "отправленные"),
    Folder.JUNK: ("spam", "junk", "junk e-mail", "спам", "нежелательная почта"),
    Folder.TRASH: ("trash", "deleted", "deleted messages", "deleted items", "корзина", "удаленные", "удалённые"),
    Folder.ARCHIVE: ("archive", "архив"),
}
CREATE_IF_MISSING = {Folder.ARCHIVE: "Archive"}

_LIST_RE = re.compile(r'^\((?P<flags>[^)]*)\)\s+(?P<delim>"[^"]*"|NIL)\s+(?P<name>.+)$')


@dataclass
class ListedFolder:
    name: str  # как на сервере (modified UTF-7)
    flags: set[str]

    @property
    def label(self) -> str:
        return utf7_decode(self.name)


def parse_list_line(line) -> ListedFolder | None:
    if isinstance(line, tuple):  # литерал: (b'(flags) "/" {12}', b'name')
        head, lit = line
        line = head.decode(errors="replace").rsplit("{", 1)[0] + quote(lit.decode(errors="replace"))
    elif isinstance(line, bytes):
        line = line.decode(errors="replace")
    m = _LIST_RE.match(line.strip())
    if not m:
        return None
    name = m.group("name").strip()
    if name.startswith('"') and name.endswith('"'):
        name = name[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return ListedFolder(name=name, flags={f.lower() for f in m.group("flags").split()})


def roles(listed: list[ListedFolder]) -> dict[str, str]:
    """Роль → имя папки на сервере. Сначала special-use (RFC 6154), потом по имени."""
    out: dict[str, str] = {Folder.INBOX: "INBOX"}
    usable = [f for f in listed if "\\noselect" not in f.flags and "\\nonexistent" not in f.flags]
    for f in usable:
        for flag, role in SPECIAL_USE.items():
            if flag in f.flags and role not in out:
                out[role] = f.name
    for role, names in BY_NAME.items():
        if role in out:
            continue
        for f in usable:
            leaf = re.split(r"[./]", f.label)[-1].strip().lower()
            if leaf in names:
                out[role] = f.name
                break
    return out


# ---------------------------------------------------------------- клиент
class ImapError(Exception):
    pass


_FETCH_UID = re.compile(rb"UID (\d+)")
_FETCH_FLAGS = re.compile(rb"FLAGS \(([^)]*)\)")
_FETCH_SIZE = re.compile(rb"RFC822\.SIZE (\d+)")
_FETCH_DATE = re.compile(rb'INTERNALDATE "([^"]+)"')


@dataclass
class Fetched:
    uid: int
    flags: set[str]
    size: int = 0
    internaldate: str = ""
    raw: bytes = b""


NETWORK_ERRORS = (imaplib.IMAP4.error, OSError, TimeoutError, ssl.SSLError, EOFError)
# imaplib.IMAP4.abort и readonly — подклассы IMAP4.error; socket.timeout — OSError.


def _guarded(fn):
    """Любая сетевая/протокольная ошибка IMAP → ImapError с понятным текстом."""

    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        try:
            return fn(self, *args, **kwargs)
        except ImapError:
            raise
        except NETWORK_ERRORS as exc:
            raise ImapError(f"{fn.__name__}: {type(exc).__name__}: {exc}"[:500]) from exc

    return wrapper


class Client:
    def __init__(self, box: Box, conn: imaplib.IMAP4 | None = None):
        self.box = box
        self.conn = conn
        self.capabilities: set[str] = set()

    # -- подключение
    def __enter__(self) -> "Client":
        if self.conn is None:
            try:
                ctx = ssl.create_default_context()
                self.conn = imaplib.IMAP4_SSL(self.box.imap_host, self.box.imap_port, ssl_context=ctx,
                                              timeout=TIMEOUT)
            except NETWORK_ERRORS as exc:
                raise ImapError(f"Нет связи с {self.box.imap_host}:{self.box.imap_port}: {exc}") from exc
        try:
            self.conn.login(self.box.address, self.box.password)
        except NETWORK_ERRORS as exc:
            self._close()  # __exit__ не вызовется — сокет закрываем сами
            if isinstance(exc, imaplib.IMAP4.error) and not isinstance(exc, imaplib.IMAP4.abort):
                raise ImapError("Сервер не принял адрес или пароль ящика") from exc
            raise ImapError(f"Обрыв связи при входе: {exc}") from exc
        caps = getattr(self.conn, "capabilities", ()) or ()
        self.capabilities = {c.decode().upper() if isinstance(c, bytes) else str(c).upper() for c in caps}
        return self

    def __exit__(self, *exc) -> None:
        try:
            self.conn.logout()
        except Exception:  # noqa: BLE001
            self._close()

    def _close(self) -> None:
        try:
            self.conn.shutdown()
        except Exception:  # noqa: BLE001
            pass

    def _ok(self, typ, data, what: str):
        if typ != "OK":
            detail = b" ".join(d for d in (data or []) if isinstance(d, bytes)).decode(errors="replace")
            raise ImapError(f"{what}: {detail or typ}")
        return data

    # -- папки
    @_guarded
    def folders(self) -> dict[str, str]:
        typ, data = self.conn.list()
        listed = [f for f in (parse_list_line(line) for line in self._ok(typ, data, "LIST") or []) if f]
        out = roles(listed)
        for role, name in CREATE_IF_MISSING.items():
            if role not in out:
                typ, _ = self.conn.create(quote(name))
                if typ == "OK":
                    out[role] = name
        return out

    @_guarded
    def select(self, name: str, readonly: bool = False) -> int:
        typ, data = self.conn.select(quote(name), readonly=readonly)
        self._ok(typ, data, f"SELECT {utf7_decode(name)}")
        typ, resp = self.conn.response("UIDVALIDITY")
        value = (resp or [None])[0]
        if value is None:
            raise ImapError("Сервер не сообщил UIDVALIDITY")
        return int(value)

    # -- поиск и чтение
    @_guarded
    def uids(self, since_uid: int = 0) -> list[int]:
        criteria = f"UID {since_uid + 1}:*" if since_uid else "ALL"
        typ, data = self.conn.uid("SEARCH", None, criteria)
        found = [int(x) for x in b" ".join(self._ok(typ, data, "SEARCH") or [b""]).split() if x.isdigit()]
        return sorted(u for u in found if u > since_uid)

    @_guarded
    def fetch_meta(self, uids: list[int]) -> dict[int, Fetched]:
        out: dict[int, Fetched] = {}
        for chunk in _chunks(uids, 200):
            typ, data = self.conn.uid("FETCH", _set(chunk), "(UID FLAGS RFC822.SIZE INTERNALDATE)")
            for item in self._ok(typ, data, "FETCH") or []:
                head = item[0] if isinstance(item, tuple) else item
                if not isinstance(head, bytes):
                    continue
                m = _FETCH_UID.search(head)
                if not m:
                    continue
                f = Fetched(uid=int(m.group(1)), flags=_flags(head))
                if s := _FETCH_SIZE.search(head):
                    f.size = int(s.group(1))
                if d := _FETCH_DATE.search(head):
                    f.internaldate = d.group(1).decode()
                out[f.uid] = f
        return out

    @_guarded
    def fetch_raw(self, uid: int, *, header_only: bool = False) -> bytes:
        what = "(BODY.PEEK[HEADER])" if header_only else "(BODY.PEEK[])"
        typ, data = self.conn.uid("FETCH", str(uid), what)
        for item in self._ok(typ, data, "FETCH") or []:
            if isinstance(item, tuple) and len(item) > 1 and isinstance(item[1], bytes):
                return item[1]
        raise ImapError(f"Письмо UID {uid} не нашлось на сервере")

    # -- изменения
    @_guarded
    def store_flag(self, uid: int, flag: str, on: bool) -> None:
        typ, data = self.conn.uid("STORE", str(uid), "+FLAGS.SILENT" if on else "-FLAGS.SILENT", f"({flag})")
        self._ok(typ, data, "STORE")

    def _expunge_one(self, uid: int) -> bool:
        """Стереть одно письмо с пометкой \\Deleted. Только UID EXPUNGE (UIDPLUS): обычный
        EXPUNGE стёр бы ВСЕ помеченные в папке — в том числе чужие пометки из почтовых программ.
        Нет UIDPLUS — письмо остаётся помеченным, сервер/программа уберут его сами."""
        if "UIDPLUS" in self.capabilities:
            typ, data = self.conn.uid("EXPUNGE", str(uid))
            self._ok(typ, data, "UID EXPUNGE")
            return True
        return False

    @_guarded
    def move(self, uid: int, target: str) -> int | None:
        """Перенести письмо; новый UID, если сервер его сообщил (COPYUID), иначе None."""
        if "MOVE" in self.capabilities:
            typ, data = self.conn.uid("MOVE", str(uid), quote(target))
            self._ok(typ, data, "MOVE")
        else:
            typ, data = self.conn.uid("COPY", str(uid), quote(target))
            self._ok(typ, data, "COPY")
            self.store_flag(uid, "\\Deleted", True)
            self._expunge_one(uid)
        return _copyuid(self.conn)

    @_guarded
    def delete(self, uid: int) -> bool:
        """True — стёрто; False — помечено \\Deleted (сервер без UIDPLUS)."""
        self.store_flag(uid, "\\Deleted", True)
        return self._expunge_one(uid)

    @_guarded
    def append(self, folder: str, raw: bytes, flags: str = "(\\Seen)") -> None:
        typ, data = self.conn.append(quote(folder), flags, imaplib.Time2Internaldate(time.time()), raw)
        self._ok(typ, data, "APPEND")


def _flags(head: bytes) -> set[str]:
    m = _FETCH_FLAGS.search(head)
    return {f.decode().lower() for f in m.group(1).split()} if m else set()


def _copyuid(conn) -> int | None:
    for key in ("COPYUID",):
        try:
            _, resp = conn.response(key)
        except Exception:  # noqa: BLE001
            return None
        for item in resp or []:
            if isinstance(item, bytes):
                parts = item.split()
                if len(parts) >= 3 and parts[-1].isdigit():
                    return int(parts[-1])
    return None


def _chunks(items: list[int], n: int):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def _set(uids: list[int]) -> str:
    return ",".join(str(u) for u in uids)
