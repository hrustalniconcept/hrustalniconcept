#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ШАГ 6 — проверка и доклад, без зависимости от офисного пакета.

Пересчитывает формулы заполненного Wordstat_Chita_analiz.xlsx движком
`formulas` (аналог headless-LibreOffice, но воспроизводимый и без GUI),
проверяет отсутствие #REF!/#DIV/0!/#VALUE! и печатает пять блоков доклада:

  1. Суммы по каждому из 10 блоков (Чита, широкое)
  2. Шесть текстовых вердиктов с листа «Свод»
  3. Соотношения: дом карповка ↔ дом смоленка чита ↔ дом засопка
  4. Сезонный размах и год-к-году по «купить дом чита»
  5. Топ-20 формулировок расширения по «коттеджный поселок чита»

Запуск:
    python verify.py Wordstat_Chita_analiz.xlsx
"""

import json
import sys
import warnings
from pathlib import Path

RAW = Path(__file__).parent / "raw_data.json"

ERROR_TOKENS = ("#REF!", "#DIV/0!", "#VALUE!", "#NAME?", "#N/A", "#NUM!", "#NULL!")

# Имена полей расширения — уточнить по фактическому JSON (ШАГ 1).
_EXP_LIST_KEYS = ("topRequests", "associations", "phrases", "items", "rows", "data")
_EXP_TEXT_KEYS = ("phrase", "text", "query", "name", "value")
_EXP_NUM_KEYS = ("count", "totalCount", "value", "shows", "number")


def recalc(xlsx):
    """Пересчёт формул; возвращает dict 'ЛИСТ'->{'A1': value}."""
    import formulas
    warnings.filterwarnings("ignore")
    model = formulas.ExcelModel().loads(str(xlsx)).finish()
    sol = model.calculate()
    out, errors = {}, []
    for key, cell in sol.items():
        # key вида "'[FILE.XLSX]СВОД'!G20"
        try:
            sheet = key.split("]", 1)[1].split("'!", 1)[0]
            coord = key.rsplit("!", 1)[1]
        except Exception:
            continue
        try:
            val = cell.value[0, 0]
        except Exception:
            val = getattr(cell, "value", None)
        out.setdefault(sheet.upper(), {})[coord] = val
        if isinstance(val, str) and any(t in val for t in ERROR_TOKENS):
            errors.append((sheet, coord, val))
    return out, errors


def g(cells, sheet, coord, default=None):
    return cells.get(sheet.upper(), {}).get(coord, default)


def fmt(v):
    if isinstance(v, float):
        return f"{v:.2f}".rstrip("0").rstrip(".")
    return v


def expansion_top(phrase, n=20):
    """Топ-N формулировок расширения по фразе из raw_data.json."""
    if not RAW.exists():
        return None
    data = json.loads(RAW.read_text(encoding="utf-8"))
    for entry in data.get("expansion", []):
        if entry.get("phrase") != phrase:
            continue
        raw = entry.get("raw") or {}
        seq = None
        for k in _EXP_LIST_KEYS:
            v = raw.get(k)
            if isinstance(v, list):
                seq = v
                break
        if seq is None:
            for wrap in ("response", "result", "data"):
                inner = raw.get(wrap)
                if isinstance(inner, dict):
                    for k in _EXP_LIST_KEYS:
                        v = inner.get(k)
                        if isinstance(v, list):
                            seq = v
                            break
                if seq is not None:
                    break
        if not seq:
            return []
        rows = []
        for it in seq:
            if not isinstance(it, dict):
                continue
            text = next((it[k] for k in _EXP_TEXT_KEYS if isinstance(it.get(k), str)), None)
            num = next((it[k] for k in _EXP_NUM_KEYS if isinstance(it.get(k), (int, float))), None)
            if text:
                rows.append((text, num))
        return rows[:n]
    return None


def main():
    if len(sys.argv) < 2:
        sys.exit("Укажите путь к xlsx: python verify.py Wordstat_Chita_analiz.xlsx")
    xlsx = Path(sys.argv[1])
    if not xlsx.exists():
        sys.exit(f"Файл не найден: {xlsx}")

    cells, errors = recalc(xlsx)

    print("=" * 64)
    print("ШАГ 6. ПРОВЕРКА И ДОКЛАД")
    print("=" * 64)

    print("\n[Проверка ошибок в ячейках]")
    if errors:
        print(f"  НАЙДЕНО {len(errors)} ячеек с ошибкой:")
        for sh, co, val in errors[:30]:
            print(f"    {sh}!{co} = {val}")
    else:
        print("  Ошибок #REF!/#DIV/0!/#VALUE! не найдено. ОК.")

    print("\n[1] Суммы по 10 блокам — Чита (широкое)")
    names = {5: "A. Категория", 6: "B. Продукт", 7: "C. Земля", 8: "D. География",
             9: "E. Фин. триггеры", 10: "F. Самострой", 11: "G. Боли/JTBD",
             12: "H. Городская альт.", 13: "I. Отток капитала", 14: "J. Бренды"}
    for r, nm in names.items():
        print(f"    {nm:22} {fmt(g(cells,'СВОД',f'B{r}'))}")
    print(f"    {'ИТОГО':22} {fmt(g(cells,'СВОД','B15'))}")

    print("\n[2] Шесть вердиктов (лист «Свод», G20..G25)")
    for r in range(20, 26):
        print(f"    G{r}: {g(cells,'СВОД',f'G{r}')}")

    print("\n[3] Соотношения по локациям (широкое, лист «Данные»)")
    k = g(cells, "ДАННЫЕ", "C28")   # дом карповка
    sm = g(cells, "ДАННЫЕ", "C30")  # дом смоленка чита
    za = g(cells, "ДАННЫЕ", "C32")  # дом засопка
    print(f"    дом карповка = {fmt(k)}, дом смоленка чита = {fmt(sm)}, дом засопка = {fmt(za)}")
    def ratio(a, b):
        try: return f"{a/max(b,1):.2f}"
        except Exception: return "—"
    if None not in (k, sm, za):
        print(f"    карповка/смоленка = {ratio(k,sm)}   карповка/засопка = {ratio(k,za)}")

    print("\n[4] «купить дом чита» — сезон и год-к-году (лист «Динамика», кол. C)")
    print(f"    Сезонный размах (пик/мин) = {fmt(g(cells,'ДИНАМИКА','C32'))}")
    yoy = g(cells, "ДИНАМИКА", "C33")
    print(f"    Год к году = {f'{yoy*100:.1f}%' if isinstance(yoy,(int,float)) else yoy}")
    print(f"    Среднее={fmt(g(cells,'ДИНАМИКА','C29'))}  Пик={fmt(g(cells,'ДИНАМИКА','C30'))}  Мин={fmt(g(cells,'ДИНАМИКА','C31'))}")

    print("\n[5] Топ-20 формулировок расширения — «коттеджный поселок чита»")
    top = expansion_top("коттеджный поселок чита", 20)
    if top is None:
        print("    raw_data.json не найден или фраза отсутствует в expansion.")
    elif not top:
        print("    Список расширения пуст/не распознан — проверьте схему ответа.")
    else:
        for i, (text, num) in enumerate(top, 1):
            tail = f"  ({num})" if num is not None else ""
            print(f"    {i:2}. {text}{tail}")


if __name__ == "__main__":
    main()
