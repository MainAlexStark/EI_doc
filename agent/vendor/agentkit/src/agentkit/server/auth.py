"""Кто говорит с агентом.

Три способа, все — Bearer:
  * личный токен пользователя (AGENT_TOKENS) — для веб-чата и скриптов;
  * токен входа от хоста (AGENT_HOST_LOGIN_SECRET) — веб-чат, встроенный в хост-приложение:
    хост подписывает «это пользователь 12, роли …, до 18:00» и отдаёт чату во фрейме,
    второго логина нет. Формат — ``akh1.<данные base64url>.<HMAC-SHA256 base64url>``,
    собирается функцией :func:`host_token` (на Python-хосте её можно просто скопировать);
  * сервисный токен хост-приложения (AGENT_SERVICE_TOKEN) + заголовки
    X-Agent-User-Id / X-Agent-User-Name / X-Agent-Roles — когда агент встроен
    в другое приложение, у которого своя авторизация (EI_doc, OpenSeller).
    Хост сам проверил пользователя и говорит агенту, от чьего имени работать.

Своя система пользователей сюда не встроена намеренно: у каждого продукта
она уже есть, и агент должен жить в ней, а не рядом.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from urllib.parse import unquote

from fastapi import HTTPException, Request

from agentkit import User


def parse_tokens(spec: str) -> dict[str, User]:
    users: dict[str, User] = {}
    for item in spec.split(","):
        item = item.strip()
        if not item:
            continue
        parts = item.split(":")
        if len(parts) < 2:
            raise ValueError(f"AGENT_TOKENS: ожидалось id:токен[:роли], получено «{item}»")
        uid, token = parts[0], parts[1]
        roles = [r for r in (parts[2].split("+") if len(parts) > 2 else []) if r]
        users[token] = User(id=uid, name=uid, roles=roles)
    return users


HOST_PREFIX = "akh1."


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def host_token(secret: str, user_id: str, *, name: str = "", roles: list[str] | None = None,
               ttl: int = 12 * 3600, now: float | None = None) -> str:
    """Токен входа, который хост выдаёт своему пользователю для веб-чата агента."""
    body = _b64(json.dumps({"sub": str(user_id), "name": name or str(user_id), "roles": roles or [],
                            "exp": int((now or time.time()) + ttl)}, ensure_ascii=False).encode())
    sig = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    return f"{HOST_PREFIX}{body}.{sig}"


def user_from_host_token(secret: str, token: str, *, now: float | None = None) -> User | None:
    try:
        body, sig = token[len(HOST_PREFIX):].split(".", 1)
        want = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, want):
            return None
        data = json.loads(_unb64(body))
    except (ValueError, TypeError):
        return None
    # Истёкший токен — 401, а не вход: чат во фрейме попросит у хоста новый (agentkit:need-token).
    if not data.get("sub") or float(data.get("exp") or 0) < (now or time.time()):
        return None
    roles = [str(r) for r in data.get("roles") or [] if str(r).strip()]
    return User(id=str(data["sub"]), name=str(data.get("name") or data["sub"]), roles=roles)


class Auth:
    def __init__(self, tokens: str, service_token: str, host_login_secret: str = ""):
        self.users = parse_tokens(tokens)
        self.service_token = service_token
        self.host_login_secret = host_login_secret

    def user_from_token(self, token: str, headers) -> User | None:
        if not token:
            return None
        if token.startswith(HOST_PREFIX):
            return user_from_host_token(self.host_login_secret, token) if self.host_login_secret else None
        if self.service_token and hmac.compare_digest(token, self.service_token):
            uid = headers.get("x-agent-user-id")
            if not uid:
                raise HTTPException(400, "сервисный токен требует заголовок X-Agent-User-Id")
            roles = [r.strip() for r in (headers.get("x-agent-roles") or "").split(",") if r.strip()]
            return User(id=uid, name=unquote(headers.get("x-agent-user-name") or uid), roles=roles)
        for t, u in self.users.items():
            if hmac.compare_digest(token, t):
                return u
        return None

    async def __call__(self, request: Request) -> User:
        h = request.headers.get("authorization", "")
        # Токен — только в заголовке: адрес попадает в журналы прокси и историю браузера.
        # Поэтому веб-чат читает SSE через fetch(), а не EventSource (тот не умеет заголовки).
        token = h[7:] if h.lower().startswith("bearer ") else ""
        user = self.user_from_token(token, request.headers)
        if user is None:
            raise HTTPException(401, "нужен токен: Authorization: Bearer <токен>")
        return user


def require_admin(user: User) -> None:
    if not user.has_role("admin"):
        raise HTTPException(403, "нужна роль admin")
