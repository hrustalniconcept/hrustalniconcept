"""Конфигурация сервиса из переменных окружения.

Секреты (токен бота, webhook-секрет) читаются только из ENV — в репозитории
их быть не должно. Образец переменных — в `.env.example`.
"""
from __future__ import annotations

import os


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _get_int(name: str, default: int | None = None) -> int | None:
    raw = _get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# --- Telegram ---
TELEGRAM_BOT_TOKEN: str = _get("TELEGRAM_BOT_TOKEN")
TELEGRAM_SALES_CHAT_ID: int | None = _get_int("TELEGRAM_SALES_CHAT_ID")
ADMIN_USER_ID: int | None = _get_int("ADMIN_USER_ID")
# Секретный путь вебхука: /telegram/<TELEGRAM_WEBHOOK_SECRET>
TELEGRAM_WEBHOOK_SECRET: str = _get("TELEGRAM_WEBHOOK_SECRET")

# Публичный базовый URL сервиса (для set_webhook), напр. https://audit.hrustalni.com
# На Railway можно прокинуть из RAILWAY_PUBLIC_DOMAIN.
PUBLIC_BASE_URL: str = _get("PUBLIC_BASE_URL") or (
    "https://" + _get("RAILWAY_PUBLIC_DOMAIN") if _get("RAILWAY_PUBLIC_DOMAIN") else ""
)
# Вешать ли webhook автоматически при старте. По брифу это действие требует
# подтверждения — поэтому управляется флагом (по умолчанию включено, но
# срабатывает только при заданном PUBLIC_BASE_URL и токене).
SET_WEBHOOK_ON_STARTUP: bool = _get("SET_WEBHOOK_ON_STARTUP", "true").lower() in (
    "1",
    "true",
    "yes",
    "on",
)

# --- Сайт и аналитика ---
ALLOWED_ORIGIN: str = _get("ALLOWED_ORIGIN", "https://audit.hrustalni.com")
YM_ID: str = _get("YM_ID")
CONSENT_VERSION: str = _get("CONSENT_VERSION", "2026-06-24")

# --- Анти-спам (rate-limit /api/lead по IP) ---
RATE_LIMIT_MAX: int = _get_int("RATE_LIMIT_MAX", 5) or 5
RATE_LIMIT_WINDOW_SEC: int = _get_int("RATE_LIMIT_WINDOW_SEC", 600) or 600

# --- Хранилище ---
# Каталог для локального лога согласий (152-ФЗ). На эфемерном хостинге это
# вспомогательный лог; долговременное доказательство согласия — задача фазы 2.
DATA_DIR: str = _get("DATA_DIR", "data")


def webhook_path() -> str:
    """Путь вебхука. Пустой секрет → дефолтный (НЕ для продакшена)."""
    secret = TELEGRAM_WEBHOOK_SECRET or "unset"
    return f"/telegram/{secret}"


def webhook_url() -> str | None:
    if not PUBLIC_BASE_URL:
        return None
    return PUBLIC_BASE_URL.rstrip("/") + webhook_path()


def bot_enabled() -> bool:
    return bool(TELEGRAM_BOT_TOKEN)
