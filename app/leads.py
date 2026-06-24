"""Лиды: схема, валидация, анти-спам и интерфейс сохранения `save_lead()`.

На старте единственное «хранилище» — карточка в Telegram-группу продаж.
Логика сохранения вынесена за интерфейс: чтобы подключить Google Sheets или БД
(фаза 2), достаточно добавить sink в `save_lead()`, не трогая остальной код.
"""
from __future__ import annotations

import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from . import config, legal

logger = logging.getLogger("hrustalni.leads")


# ----------------------------- Схема входа формы -----------------------------
class LeadIn(BaseModel):
    """Тело запроса POST /api/lead."""

    name: str = Field(default="", max_length=120)
    phone: str = Field(default="", max_length=40)
    region: str = Field(default="", max_length=160)
    stage: str = Field(default="", max_length=160)
    comment: str | None = Field(default=None, max_length=2000)
    consent: bool = False
    hp: str = Field(default="", max_length=200)  # honeypot — должен быть пустым
    page_url: str = Field(default="", max_length=500)


@dataclass
class Lead:
    """Внутреннее представление лида."""

    name: str
    phone: str
    region: str
    stage: str
    source: str  # "site" | "bot" | "bot-case"
    consent: bool
    consent_version: str = legal.CONSENT_VERSION
    comment: str = ""
    page_url: str = ""
    ip: str = ""
    user_agent: str = ""
    tg_username: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )


# ----------------------------- Валидация -----------------------------
class LeadValidationError(ValueError):
    """Ошибка валидации лида (текст безопасно показать пользователю)."""


def normalize_phone_ru(raw: str) -> str | None:
    """Нормализует телефон РФ к виду +7XXXXXXXXXX. None — если некорректный."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits[0] == "8":
        digits = "7" + digits[1:]
    if len(digits) == 10:  # без кода страны
        digits = "7" + digits
    if len(digits) == 11 and digits[0] == "7":
        return "+" + digits
    return None


def validate_lead_input(data: LeadIn) -> Lead:
    """Серверная валидация заявки с сайта. Бросает LeadValidationError."""
    name = (data.name or "").strip()
    if len(name) < 2:
        raise LeadValidationError("Укажите имя (не короче 2 символов).")

    phone = normalize_phone_ru(data.phone)
    if not phone:
        raise LeadValidationError("Укажите корректный номер телефона.")

    if not data.consent:
        raise LeadValidationError("Без согласия на обработку данных отправка невозможна.")

    return Lead(
        name=name,
        phone=phone,
        region=(data.region or "").strip(),
        stage=(data.stage or "").strip(),
        comment=(data.comment or "").strip(),
        source="site",
        consent=True,
        page_url=(data.page_url or "").strip(),
    )


# ----------------------------- Анти-спам -----------------------------
class RateLimiter:
    """Простой in-memory rate-limit по ключу (IP). Достаточно для MVP (1 процесс)."""

    def __init__(self, max_hits: int, window_sec: int):
        self.max_hits = max_hits
        self.window_sec = window_sec
        self._hits: dict[str, list[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        window_start = now - self.window_sec
        hits = [t for t in self._hits.get(key, []) if t >= window_start]
        if len(hits) >= self.max_hits:
            self._hits[key] = hits
            return False
        hits.append(now)
        self._hits[key] = hits
        return True


rate_limiter = RateLimiter(config.RATE_LIMIT_MAX, config.RATE_LIMIT_WINDOW_SEC)


def is_honeypot_tripped(data: LeadIn) -> bool:
    return bool((data.hp or "").strip())


# ----------------------------- Сохранение лида -----------------------------
async def save_lead(lead: Lead) -> str:
    """Единая точка сохранения лида.

    На старте: фиксируем согласие (152-ФЗ) и отправляем карточку в Telegram.
    Фаза 2: сюда же добавляется запись в Google Sheets / БД — без правок вызовов.
    """
    # 1) Доказательство согласия
    legal.record_consent(
        legal.ConsentRecord(
            lead_id=lead.id,
            consent_version=lead.consent_version,
            created_at=lead.created_at,
            source=lead.source,
            name=lead.name,
            phone=lead.phone,
            ip=lead.ip,
            user_agent=lead.user_agent,
        )
    )

    # 2) Доставка заявки (MVP-хранилище — Telegram-группа)
    from . import telegram  # ленивый импорт, чтобы избежать цикла

    try:
        await telegram.send_lead_card(lead)
    except Exception:  # noqa: BLE001 — не теряем заявку из-за сбоя доставки
        logger.exception("Не удалось отправить карточку лида %s в Telegram", lead.id)
        raise

    logger.info("Лид сохранён id=%s source=%s", lead.id, lead.source)
    return lead.id
