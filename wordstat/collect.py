#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Сбор частотности Wordstat по семантическому ядру проекта «Чита».

Использует Wordstat API в составе Yandex Search API (Yandex Cloud / AI Studio).
Документация: https://yandex.cloud/ru/docs/search-api/concepts/wordstat

ПОДГОТОВКА (делается один раз, ~15 минут + сутки на одобрение заявки):
  1. Завести аккаунт в Yandex Cloud, создать каталог (folder).
  2. В AI Studio получить API-ключ (тот же ключ, что для YandexGPT).
  3. Подать заявку на доступ к Wordstat, если сервис её потребует.
  4. Записать в переменные окружения:
        export YC_API_KEY="AQVN..."
        export YC_FOLDER_ID="b1g..."

ЗАПУСК:
  python collect.py verify-regions     # найти ID регионов Чита / Забайкалье / Иркутск
  python collect.py collect            # основной сбор -> raw_data.json
  python collect.py collect --dry-run  # посчитать, сколько запросов уйдёт, ничего не тратя

Скрипт складывает сырой ответ в raw_data.json. Отчёт строит build_report.py.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests

from keywords import BLOCKS, DYNAMICS_PHRASES, EXPANSION_PHRASES, all_phrases

BASE = "https://searchapi.api.cloud.yandex.net/v2/wordstat"
OUT = Path(__file__).parent / "raw_data.json"

# --------------------------------------------------------------------------
# РЕГИОНЫ — ОБЯЗАТЕЛЬНО ПРОВЕРИТЬ ПЕРЕД СБОРОМ
# Запустите `python collect.py verify-regions` и подставьте фактические ID.
# Значения ниже — предположительные, вслепую им доверять нельзя.
# --------------------------------------------------------------------------
REGIONS = {
    "Чита": [76],              # TODO проверить
    "Забайкальский край": [],  # TODO заполнить после verify-regions
    "Иркутск": [63],           # TODO проверить — контрольный регион
}

RATE_DELAY = 0.25   # пауза между запросами, сек (лимит ~10 rps)
MAX_RETRIES = 4


def api_key():
    key = os.environ.get("YC_API_KEY")
    if not key:
        sys.exit("Не задана переменная окружения YC_API_KEY")
    return key


def folder_id():
    fid = os.environ.get("YC_FOLDER_ID")
    if not fid:
        sys.exit("Не задана переменная окружения YC_FOLDER_ID")
    return fid


def post(method, payload, attempt=1):
    """Один POST к Wordstat API с ретраями на 429/5xx."""
    url = f"{BASE}/{method}"
    headers = {
        "Authorization": f"Api-Key {api_key()}",
        "Content-Type": "application/json",
    }
    body = dict(payload)
    body["folderId"] = folder_id()

    try:
        r = requests.post(url, headers=headers, json=body, timeout=45)
    except requests.RequestException as e:
        if attempt <= MAX_RETRIES:
            time.sleep(2 ** attempt)
            return post(method, payload, attempt + 1)
        return {"_error": f"network: {e}"}

    if r.status_code == 200:
        return r.json()

    if r.status_code in (429, 500, 502, 503, 504) and attempt <= MAX_RETRIES:
        wait = 2 ** attempt
        print(f"    HTTP {r.status_code}, повтор через {wait}с", file=sys.stderr)
        time.sleep(wait)
        return post(method, payload, attempt + 1)

    return {"_error": f"HTTP {r.status_code}: {r.text[:300]}"}


# --------------------------------------------------------------------------
# ИЗВЛЕЧЕНИЕ ЧАСТОТНОСТИ ИЗ ОТВЕТА topRequests
# ВНИМАНИЕ: реальное имя поля общей частотности подтверждается на ШАГЕ 1
# (пробный запрос + фактический JSON). До этого — перебор наиболее вероятных
# имён. Полный сырой ответ всё равно сохраняется в raw_data.json, поэтому даже
# при неверной догадке ничего не теряется и всё пересчитывается из raw.
# --------------------------------------------------------------------------
_TOTAL_KEYS = ("totalCount", "count", "total", "value", "shows", "amount")


def extract_total(data):
    """
    Возвращает (число|None, below_threshold: bool).
    None + below_threshold=False  -> поле не распознано (проверить схему)
    None + below_threshold=True   -> успешный ответ, но частотность ниже порога
    Правило проекта: «меньше 10» -> 0, поэтому below_threshold трактуется как 0
    на этапе переноса в таблицу.
    """
    if not isinstance(data, dict) or "_error" in data:
        return None, False
    # прямые числовые поля верхнего уровня
    for k in _TOTAL_KEYS:
        v = data.get(k)
        if isinstance(v, (int, float)):
            return int(v), False
    # частая обёртка вида {"topRequests": {...}} или {"response": {...}}
    for wrap in ("topRequests", "response", "result", "data"):
        inner = data.get(wrap)
        if isinstance(inner, dict):
            for k in _TOTAL_KEYS:
                v = inner.get(k)
                if isinstance(v, (int, float)):
                    return int(v), False
    # успешный ответ без числа частотности — почти наверняка «ниже порога показа»
    return None, True


def cmd_verify_regions(args):
    """
    Выводит распределение по регионам для опорной фразы.
    Найдите в списке Читу, Забайкальский край и Иркутск, впишите их ID в REGIONS.
    """
    print("Запрашиваю распределение по регионам для фразы 'купить дом'...\n")
    data = post("regions", {"phrase": "купить дом"})
    if "_error" in data:
        sys.exit(f"Ошибка: {data['_error']}")

    print(json.dumps(data, ensure_ascii=False, indent=2)[:4000])
    print("\n--- Ищите строки со словами Чита / Забайкал / Иркутск ---")
    print("Найденные ID впишите в словарь REGIONS в начале collect.py")


def cmd_collect(args):
    phrases = all_phrases()
    active_regions = {k: v for k, v in REGIONS.items() if v}

    if not active_regions:
        sys.exit("Не заполнен ни один регион в REGIONS. Сначала: python collect.py verify-regions")

    n_top = len(phrases) * len(active_regions)
    n_dyn = len(DYNAMICS_PHRASES) * len(active_regions)
    n_exp = len(EXPANSION_PHRASES)
    total = n_top + n_dyn + n_exp

    print(f"Фраз в ядре:        {len(phrases)}")
    print(f"Регионов:           {len(active_regions)} ({', '.join(active_regions)})")
    print(f"Запросов topRequests: {n_top}")
    print(f"Запросов dynamics:    {n_dyn}")
    print(f"Запросов расширения:  {n_exp}")
    print(f"ИТОГО запросов:       {total}")
    print(f"Оценка времени:       ~{int(total * (RATE_DELAY + 0.6) / 60) + 1} мин\n")

    if args.dry_run:
        print("Режим --dry-run, ничего не отправлено.")
        return

    result = {
        "meta": {
            "collected_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "regions": active_regions,
            "phrase_count": len(phrases),
        },
        "frequency": [],
        "dynamics": [],
        "expansion": [],
    }

    # --- 1. Частотность за 30 дней ---
    print("[1/3] Частотность")
    for i, (block, phrase) in enumerate(phrases, 1):
        for region_name, region_ids in active_regions.items():
            data = post("topRequests", {
                "phrase": phrase,
                "regions": region_ids,
                "numPhrases": 1,
            })
            total, below = extract_total(data)
            row = {
                "block": block,
                "phrase": phrase,
                "region": region_name,
                "total_count": total,
                "below_threshold": below,
                "error": data.get("_error"),
                # Полный сырой ответ — требование «ничего не выбрасывать».
                # Отсюда to_xlsx перепроверит частотность, если имя поля уточнится.
                "raw": data,
            }
            result["frequency"].append(row)
            time.sleep(RATE_DELAY)
        done = f"{i}/{len(phrases)}"
        print(f"  {done:>8}  {phrase}")

    # --- 2. Динамика ---
    print("\n[2/3] Динамика")
    for phrase in DYNAMICS_PHRASES:
        for region_name, region_ids in active_regions.items():
            dyn_payload = {
                "phrase": phrase,
                "regions": region_ids,
                "period": "monthly",
            }
            # ШАГ 1: если ответ по умолчанию отдаёт меньше 24 месяцев, здесь
            # добавляется диапазон дат. Реальные имена полей (fromDate/toDate,
            # startDate/endDate и формат) подтвердить по фактическому JSON и
            # раскомментировать нужный вариант:
            # dyn_payload["fromDate"] = "2024-09-01"
            # dyn_payload["toDate"]   = "2026-08-31"
            data = post("dynamics", dyn_payload)
            result["dynamics"].append({
                "phrase": phrase,
                "region": region_name,
                "raw": data,
            })
            time.sleep(RATE_DELAY)
        print(f"  {phrase}")

    # --- 3. Расширение семантики ---
    print("\n[3/3] Расширение семантики")
    main_region = list(active_regions.values())[0]
    for phrase in EXPANSION_PHRASES:
        data = post("topRequests", {
            "phrase": phrase,
            "regions": main_region,
            "numPhrases": 100,
        })
        result["expansion"].append({"phrase": phrase, "raw": data})
        time.sleep(RATE_DELAY)
        print(f"  {phrase}")

    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nГотово. Сохранено в {OUT}")

    # --- Контроль качества сбора (ШАГ 4): порог ошибок 5% ---
    freq = result["frequency"]
    errors = [r for r in freq if r.get("error")]
    dyn_err = [d for d in result["dynamics"] if isinstance(d.get("raw"), dict) and "_error" in d["raw"]]
    exp_err = [e for e in result["expansion"] if isinstance(e.get("raw"), dict) and "_error" in e["raw"]]
    all_calls = len(freq) + len(result["dynamics"]) + len(result["expansion"])
    all_err = len(errors) + len(dyn_err) + len(exp_err)
    err_rate = (all_err / all_calls) if all_calls else 0.0

    # Успешные ответы, где частотность так и не распозналась — сигнал о расхождении схемы.
    unrec = [r for r in freq if not r.get("error")
             and r.get("total_count") is None and not r.get("below_threshold")]

    print(f"Ошибок: {all_err}/{all_calls} ({err_rate*100:.1f}%)")
    if unrec:
        print(f"ВНИМАНИЕ: {len(unrec)} успешных ответов без распознанной частотности — "
              f"вероятно, отличается имя поля. Проверьте raw и функцию extract_total().")

    if err_rate > 0.05:
        print(f"\nСТОП: доля ошибок {err_rate*100:.1f}% > 5%. Не продолжаю до разбора.")
        for r in (errors + dyn_err + exp_err)[:15]:
            tag = r.get("phrase", "?")
            msg = r.get("error") or (r.get("raw") or {}).get("_error")
            print(f"  - {tag}: {msg}")
        sys.exit(2)

    if errors:
        print(f"Единичные ошибки ({len(errors)}). Пример: {errors[0]['error']}")
    print("Дальше: python to_xlsx.py Wordstat_Chita_analiz.xlsx — перенос в лист «Данные» и «Динамика».")


def main():
    p = argparse.ArgumentParser(description="Сбор Wordstat по проекту Чита")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("verify-regions", help="найти ID регионов")

    c = sub.add_parser("collect", help="основной сбор")
    c.add_argument("--dry-run", action="store_true", help="только посчитать объём")

    args = p.parse_args()
    if args.cmd == "verify-regions":
        cmd_verify_regions(args)
    else:
        cmd_collect(args)


if __name__ == "__main__":
    main()
