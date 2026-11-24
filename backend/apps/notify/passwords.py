"""Правила пароля и закрытие входов — как в openseller (internal/auth, store.SetUserPassword).

Пароль: 8…256 символов, не из списка частых, не совпадает с именем почты
и не один повторяющийся символ (NIST 800-63B — без «обязательно спецсимвол»).
Поверх — стандартные валидаторы Django из AUTH_PASSWORD_VALIDATORS
(похожесть на почту, частые пароли, только цифры).

После смены пароля закрываются все остальные входы (полевые телефоны тоже:
пароль меняют, когда его могли узнать), и сразу — не через 15 минут:
* в каждом токене метка пароля (CHECK_REVOKE_TOKEN): токен со старой меткой —
  и access, и refresh — отклоняется на первом же запросе (apps.notify.actor);
* токены без метки (выданы до этой версии) отклоняются, если выданы раньше
  AccountSecurity.password_changed_at — время ставит сигнал на любую смену пароля;
* refresh-токены из OutstandingToken дополнительно уходят в чёрный список.
Текущему устройству выдаётся новая пара токенов.
"""

from __future__ import annotations

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

MIN_LEN = 8
MAX_LEN = 256

# Короткий список из openseller плюс свои — то, что первым приходит в голову в фирме.
COMMON = {
    "12345678", "123456789", "1234567890", "password", "password1", "qwertyui", "qwerty123",
    "11111111", "00000000", "йцукенгш", "qwertyuiop", "1q2w3e4r", "1qaz2wsx", "87654321",
    "abcdefgh", "admin123", "eidoc123", "ei_doc123", "поверка123", "единица1",
}


def check(password: str, user=None) -> list[str]:
    """Ошибки для человека; пустой список — пароль подходит."""
    errors: list[str] = []
    n = len(password or "")
    if n < MIN_LEN:
        return [f"Пароль — не короче {MIN_LEN} символов."]
    if n > MAX_LEN:
        return [f"Пароль — не длиннее {MAX_LEN} символов."]
    low = password.lower()
    weak = "Пароль слишком простой — добавьте буквы и цифры или сделайте длиннее."
    email = (getattr(user, "email", "") or "").lower()
    if low in COMMON or low.count(low[0]) == n or (email and low == email.split("@", 1)[0]):
        return [weak]
    try:
        validate_password(password, user=user)
    except ValidationError as exc:
        for message in exc.messages:
            if message not in errors:
                errors.append(message)
    return errors


def revoke_all_sessions(user) -> int:
    """Все refresh-токены пользователя — в чёрный список. Возвращает, сколько закрыто."""
    closed = 0
    for token in OutstandingToken.objects.filter(user=user).exclude(blacklistedtoken__isnull=False):
        BlacklistedToken.objects.get_or_create(token=token)
        closed += 1
    return closed


def new_tokens(user) -> dict:
    refresh = RefreshToken.for_user(user)
    return {"access": str(refresh.access_token), "refresh": str(refresh)}


def set_password(user, password: str) -> int:
    """Сменить пароль и закрыть все входы. Возвращает, сколько входов закрыто."""
    user.set_password(password)
    user.save(update_fields=["password"])
    return revoke_all_sessions(user)
