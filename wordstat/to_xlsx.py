#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Переносит raw_data.json (результат collect.py) в файл
Wordstat_Chita_analiz.xlsx:
  • лист «Данные»   — частотность по трём регионам (широкое соответствие);
  • лист «Динамика» — помесячные значения за 24 месяца, регион Чита.

Формулы на листах «Свод» и «Динамика» пересчитаются при открытии в Excel /
LibreOffice. Скрипт НЕ трогает формулы и структуру — пишет только значения.

Запуск:
    python to_xlsx.py Wordstat_Chita_analiz.xlsx

Правила проекта:
  • «меньше 10» (ниже порога показа) записывается как 0, а не как пропуск —
    ноль по категорийному запросу это результат, а не отсутствие данных;
  • пропуск (ячейка не заполняется) только при фактической ошибке запроса.
"""

import json
import re
import sys
from pathlib import Path

from openpyxl import load_workbook

from keywords import DYNAMICS_PHRASES, all_phrases

RAW = Path(__file__).parent / "raw_data.json"

# Колонка листа «Данные» для «широкого» соответствия каждого региона.
# Именно эти колонки (C, F, H) суммирует лист «Свод», поэтому переносим их.
COL = {
    "Чита": 3,                # C — Чита, широкое
    "Забайкальский край": 6,  # F — Край, широкое
    "Иркутск": 8,             # H — Иркутск, широкое
}

# Регион, по которому лист «Динамика» ждёт помесячные ряды.
DYNAMICS_REGION = "Чита"

# --------------------------------------------------------------------------
# Имена числовых полей. Подтверждаются на ШАГЕ 1 по фактическому JSON.
# Держим перебор, т.к. в raw_data.json лежит полный сырой ответ и мы можем
# перепроверить частотность, даже если collect.py угадал имя поля неточно.
# --------------------------------------------------------------------------
_NUM_KEYS = ("totalCount", "count", "total", "value", "shows", "amount")
_SERIES_KEYS = ("dynamics", "graph", "points", "series", "rows", "items", "data")
_PERIOD_KEYS = ("period", "date", "month", "yearMonth", "periodStart", "from")


def _num(value):
    """Число из значения ячейки API. '<10' и None трактуем как 0 (правило проекта)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        s = value.strip().replace(" ", "").replace(" ", "")
        if s in ("", "—", "-"):
            return None
        if s.startswith("<"):      # «<10» -> ниже порога -> 0
            return 0
        s = s.replace(",", ".")
        try:
            return int(float(s))
        except ValueError:
            return None
    return None


def extract_total(raw):
    """
    (число|None, below_threshold). Повторяет логику collect.extract_total,
    но работает напрямую из сырого ответа, лежащего в raw_data.json.
    """
    if not isinstance(raw, dict) or "_error" in raw:
        return None, False
    for k in _NUM_KEYS:
        n = _num(raw.get(k))
        if n is not None:
            return n, False
    for wrap in ("topRequests", "response", "result", "data"):
        inner = raw.get(wrap)
        if isinstance(inner, dict):
            for k in _NUM_KEYS:
                n = _num(inner.get(k))
                if n is not None:
                    return n, False
    return None, True  # успешный ответ без числа — «ниже порога» -> 0


def freq_value(rec):
    """
    Значение для листа «Данные» по одной записи frequency.
    Возвращает (int|None, skip: bool). skip=True — только при ошибке/нераспознанной схеме.
    """
    if rec.get("error"):
        return None, True
    # 1) то, что уже извлёк collect.py
    if rec.get("total_count") is not None:
        return int(rec["total_count"]), False
    if rec.get("below_threshold"):
        return 0, False
    # 2) перепроверка из сырого ответа (на случай уточнённого имени поля)
    total, below = extract_total(rec.get("raw"))
    if total is not None:
        return total, False
    if below:
        return 0, False
    # 3) успешный ответ, но частотность не распозналась — сигнал о схеме.
    return None, True


# --------------------------------------------------------------------------
# ДИНАМИКА
# --------------------------------------------------------------------------
_MONTHS_RE = re.compile(r"(\d{4})[-./](\d{1,2})|(\d{1,2})[.](\d{4})")


def norm_period(value):
    """
    Приводит период из ответа API к 'MM.YYYY' (как в колонке A листа «Динамика»).
    Понимает 'YYYY-MM-DD', 'YYYY-MM', 'MM.YYYY', 'YYYY/MM'. Иначе — None.
    """
    if isinstance(value, dict):
        for k in _PERIOD_KEYS:
            if k in value:
                return norm_period(value[k])
        return None
    if not isinstance(value, str):
        return None
    m = _MONTHS_RE.search(value.strip())
    if not m:
        return None
    if m.group(1):          # YYYY-MM...
        year, month = m.group(1), int(m.group(2))
    else:                   # MM.YYYY
        month, year = int(m.group(3)), m.group(4)
    return f"{month:02d}.{year}"


def extract_series(raw):
    """
    Из сырого ответа dynamics вытаскивает [(period 'MM.YYYY', value int), ...].
    Схему полей подтвердить на ШАГЕ 1; парсер намеренно терпимый.
    """
    if not isinstance(raw, dict) or "_error" in raw:
        return []
    seq = None
    for k in _SERIES_KEYS:
        v = raw.get(k)
        if isinstance(v, list):
            seq = v
            break
    if seq is None:  # ряд может лежать под обёрткой
        for wrap in ("response", "result", "data"):
            inner = raw.get(wrap)
            if isinstance(inner, dict):
                for k in _SERIES_KEYS:
                    v = inner.get(k)
                    if isinstance(v, list):
                        seq = v
                        break
            if seq is not None:
                break
    if not seq:
        return []

    out = []
    for point in seq:
        if not isinstance(point, dict):
            continue
        period = None
        for k in _PERIOD_KEYS:
            if k in point:
                period = norm_period(point[k])
                if period:
                    break
        if not period:
            continue
        val = None
        for k in _NUM_KEYS:
            if k in point:
                val = _num(point[k])
                if val is not None:
                    break
        if val is None:
            val = 0  # успешная точка без числа -> ниже порога -> 0
        out.append((period, val))
    return out


def write_frequency(data, wb):
    ws = wb["Данные"]
    row_of = {phrase: i + 2 for i, (_b, phrase) in enumerate(all_phrases())}

    written = skipped = unrecognized = 0
    for rec in data.get("frequency", []):
        r = row_of.get(rec.get("phrase"))
        c = COL.get(rec.get("region"))
        if not r or not c:
            skipped += 1
            continue
        val, skip = freq_value(rec)
        if skip:
            skipped += 1
            if not rec.get("error"):
                unrecognized += 1
            continue
        ws.cell(row=r, column=c, value=int(val))
        written += 1
    return written, skipped, unrecognized


def write_dynamics(data, wb):
    ws = wb["Динамика"]

    # Заголовки фраз лежат в строке 3, колонки B..I. Строим фраза -> колонка,
    # сверяясь с фактическими заголовками листа, а не полагаясь на порядок.
    phrase_col = {}
    for col in range(2, 2 + len(DYNAMICS_PHRASES)):
        head = ws.cell(row=3, column=col).value
        if head:
            phrase_col[str(head).strip()] = col

    # Метки месяцев уже стоят в колонке A (строки 4..27). Строим метка -> строка.
    month_row = {}
    for r in range(4, 28):
        label = ws.cell(row=r, column=1).value
        if label:
            month_row[str(label).strip()] = r

    written = 0
    misses = []
    unmatched_phrases = []
    for entry in data.get("dynamics", []):
        if entry.get("region") != DYNAMICS_REGION:
            continue
        phrase = entry.get("phrase")
        col = phrase_col.get(phrase)
        if not col:
            unmatched_phrases.append(phrase)
            continue
        series = extract_series(entry.get("raw"))
        if not series:
            misses.append(phrase)
            continue
        for period, val in series:
            r = month_row.get(period)
            if r:
                ws.cell(row=r, column=col, value=int(val))
                written += 1
    return written, misses, unmatched_phrases


def stamp_instruction(data, wb):
    ws_i = wb["Инструкция"]
    for row in ws_i.iter_rows(min_col=2, max_col=3):
        if row[0].value == "Дата сбора:":
            row[1].value = data.get("meta", {}).get("collected_at", "")
        if row[0].value == "Кто собирал:":
            row[1].value = "Wordstat API, автоматический сбор"


def main():
    if len(sys.argv) < 2:
        sys.exit("Укажите путь к xlsx: python to_xlsx.py Wordstat_Chita_analiz.xlsx")
    xlsx = Path(sys.argv[1])
    if not xlsx.exists():
        sys.exit(f"Файл не найден: {xlsx}")
    if not RAW.exists():
        sys.exit(f"Нет данных: {RAW}. Сначала запустите collect.py")

    data = json.loads(RAW.read_text(encoding="utf-8"))
    wb = load_workbook(xlsx)

    f_written, f_skipped, f_unrec = write_frequency(data, wb)
    d_written, d_misses, d_unmatched = write_dynamics(data, wb)
    stamp_instruction(data, wb)

    wb.save(xlsx)

    print(f"«Данные»:   записано {f_written}, пропущено {f_skipped}")
    if f_unrec:
        print(f"  ВНИМАНИЕ: {f_unrec} успешных ответов без распознанной частотности "
              f"— проверьте имя поля в raw_data.json.")
    print(f"«Динамика»: записано {d_written} значений по региону «{DYNAMICS_REGION}»")
    if d_unmatched:
        print(f"  ВНИМАНИЕ: заголовок не сопоставлен для фраз: {d_unmatched}")
    if d_misses:
        print(f"  ВНИМАНИЕ: пустой/нераспознанный ряд динамики для: {d_misses}")
    print(f"Файл обновлён: {xlsx}")


if __name__ == "__main__":
    main()
