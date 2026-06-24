"""Юридический контур 152-ФЗ: фиксация факта согласия на обработку ПДн.

С каждым лидом сохраняем доказательство согласия: версию текста согласия,
дату/время, IP, user-agent и источник. На MVP пишем в локальный JSONL-лог
(вспомогательный) — основной канал заявок на старте это Telegram. Для строгого
152-ФЗ долговременное хранение настраивается в фазе 2 (БД на РФ-хостинге / Sheets).
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass

from . import config

logger = logging.getLogger("hrustalni.legal")

CONSENT_VERSION = config.CONSENT_VERSION

_CONSENT_LOG = os.path.join(config.DATA_DIR, "consent_log.jsonl")


@dataclass
class ConsentRecord:
    lead_id: str
    consent_version: str
    created_at: str  # ISO 8601
    source: str  # "site" | "bot" | ...
    name: str
    phone: str
    ip: str = ""
    user_agent: str = ""


def record_consent(record: ConsentRecord) -> None:
    """Записать доказательство согласия в локальный лог (best-effort)."""
    try:
        os.makedirs(config.DATA_DIR, exist_ok=True)
        with open(_CONSENT_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
    except OSError as exc:  # лог не должен ронять обработку заявки
        logger.warning("Не удалось записать согласие в лог: %s", exc)
