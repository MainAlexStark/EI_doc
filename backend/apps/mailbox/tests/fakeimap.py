"""Поддельный IMAP-сервер в памяти: те же вызовы imaplib, что делает apps.mailbox.imap.Client."""

from __future__ import annotations

import re


class FakeIMAP:
    def __init__(self, *, move: bool = True, uidplus: bool = True):
        caps = ["IMAP4REV1"] + (["MOVE"] if move else []) + (["UIDPLUS"] if uidplus else [])
        self.capabilities = tuple(caps)
        self.folders: dict[str, dict] = {}
        self.flags_of: dict[str, set] = {}
        self.selected: str | None = None
        self._responses: dict[str, list] = {}
        self.logged_in = False
        self.password = "secret"
        self.add_folder("INBOX")

    # -- наполнение
    def add_folder(self, name: str, flags: str = "", uidvalidity: int = 1000) -> None:
        self.folders[name] = {"uidvalidity": uidvalidity, "next": 1, "msgs": {}}
        self.flags_of[name] = set(flags.split())

    def deliver(self, folder: str, raw: bytes, flags=()) -> int:
        f = self.folders[folder]
        uid = f["next"]
        f["next"] += 1
        f["msgs"][uid] = {"raw": raw, "flags": {x.lower() for x in flags}}
        return uid

    # -- imaplib API
    def login(self, user, password):
        if password != self.password:
            import imaplib
            raise imaplib.IMAP4.error("AUTHENTICATIONFAILED")
        self.logged_in = True
        return "OK", [b"Logged in"]

    def logout(self):
        return "BYE", [b""]

    def list(self):
        out = []
        for name in self.folders:
            flags = " ".join(["\\HasNoChildren", *self.flags_of[name]])
            out.append(f'({flags}) "/" "{name}"'.encode())
        return "OK", out

    def create(self, name):
        name = name.strip('"')
        if name not in self.folders:
            self.add_folder(name)
        return "OK", [b"CREATE completed"]

    def select(self, name, readonly=False):
        name = name.strip('"')
        if name not in self.folders:
            return "NO", [b"no such folder"]
        self.selected = name
        self._responses["UIDVALIDITY"] = [str(self.folders[name]["uidvalidity"]).encode()]
        return "OK", [str(len(self.folders[name]["msgs"])).encode()]

    def response(self, key):
        return "OK", self._responses.pop(key, [None])

    def _uids(self, spec: str) -> list[int]:
        msgs = self.folders[self.selected]["msgs"]
        out = []
        for piece in spec.split(","):
            if ":" in piece:
                lo, hi = piece.split(":")
                lo = int(lo)
                hi = max(msgs) if hi == "*" and msgs else (int(hi) if hi != "*" else lo)
                out += [u for u in msgs if lo <= u <= hi]
            elif piece.isdigit() and int(piece) in msgs:
                out.append(int(piece))
        return sorted(set(out))

    def uid(self, cmd, *args):
        cmd = cmd.upper()
        folder = self.folders[self.selected]
        msgs = folder["msgs"]
        if cmd == "SEARCH":
            crit = args[1]
            uids = sorted(msgs) if crit == "ALL" else self._uids(crit.split()[1])
            return "OK", [" ".join(map(str, uids)).encode()]
        if cmd == "FETCH":
            spec, what = args
            out = []
            for i, uid in enumerate(self._uids(spec), 1):
                m = msgs[uid]
                flags = " ".join(sorted(m["flags"]))
                if "BODY.PEEK" in what:
                    raw = m["raw"].split(b"\r\n\r\n", 1)[0] + b"\r\n\r\n" if "HEADER" in what else m["raw"]
                    out.append((f"{i} (UID {uid} BODY[] {{{len(raw)}}}".encode(), raw))
                    out.append(b")")
                else:
                    out.append(f'{i} (UID {uid} FLAGS ({flags}) RFC822.SIZE {len(m["raw"])} '
                               f'INTERNALDATE "17-Jul-2026 10:00:00 +0300")'.encode())
            return "OK", out
        if cmd == "STORE":
            uid, op, flags = args
            names = {f.lower() for f in re.findall(r"\\\w+", flags)}
            for u in self._uids(uid):
                if op.startswith("+"):
                    msgs[u]["flags"] |= names
                else:
                    msgs[u]["flags"] -= names
            return "OK", [b""]
        if cmd in ("MOVE", "COPY"):
            uid, target = args
            target = target.strip('"')
            if target not in self.folders:
                return "NO", [b"no such folder"]
            new = None
            for u in self._uids(uid):
                new = self.deliver(target, msgs[u]["raw"], msgs[u]["flags"])
                if cmd == "MOVE":
                    del msgs[u]
            self._responses["COPYUID"] = [f"{self.folders[target]['uidvalidity']} {uid} {new}".encode()]
            return "OK", [b"done"]
        if cmd == "EXPUNGE":
            for u in self._uids(args[0]):
                if "\\deleted" in msgs[u]["flags"]:
                    del msgs[u]
            return "OK", [b""]
        raise AssertionError(f"неожиданная команда {cmd}")

    def expunge(self):
        msgs = self.folders[self.selected]["msgs"]
        for u in [u for u, m in msgs.items() if "\\deleted" in m["flags"]]:
            del msgs[u]
        return "OK", [b""]

    def append(self, name, flags, date, raw):
        name = name.strip('"')
        fl = {f.lower() for f in re.findall(r"\\\w+", flags or "")}
        self.deliver(name, raw, fl)
        return "OK", [b"APPEND completed"]
