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
            row = {
                "block": block,
                "phrase": phrase,
                "region": region_name,
                "total_count": data.get("totalCount"),
                "error": data.get("_error"),
            }
            result["frequency"].append(row)
            time.sleep(RATE_DELAY)
        done = f"{i}/{len(phrases)}"
        print(f"  {done:>8}  {phrase}")

    # --- 2. Динамика ---
    print("\n[2/3] Динамика")
    for phrase in DYNAMICS_PHRASES:
        for region_name, region_ids in active_regions.items():
            data = post("dynamics", {
                "phrase": phrase,
                "regions": region_ids,
                "period": "monthly",
            })
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

    errors = [r for r in result["frequency"] if r.get("error")]
    print(f"\nГотово. Сохранено в {OUT}")
    if errors:
        print(f"ВНИМАНИЕ: {len(errors)} запросов с ошибкой. Пример: {errors[0]['error']}")
    print("Дальше: python to_xlsx.py — перенос в лист «Данные» файла Wordstat_Chita_analiz.xlsx")


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
