#!/usr/bin/env python3
"""Сборка статической версии сайта для GitHub Pages.

Единственный источник — каталог `web/` (тот же, что раздаёт FastAPI). Здесь мы
переписываем корневые пути (`/assets`, `/privacy`, `/cookie`) на относительные,
чтобы сайт работал и на project-страницах (`user.github.io/repo/`), и позже на
своём домене. Бэкенд (`/api/lead`, бот) на Pages не запускается — адрес API
задаётся через `window.__API_BASE` (env API_BASE), по умолчанию пусто.

Запуск: python scripts/build_static.py [output_dir]
"""
from __future__ import annotations

import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web")

API_BASE = os.environ.get("API_BASE", "").strip()
YM_ID = os.environ.get("YM_ID", "").strip()


def _ym_snippet() -> str:
    if not YM_ID:
        return ""
    return (
        f"<script>window.__YM_ID={YM_ID};</script>\n"
        '<script type="text/javascript">\n'
        "(function(m,e,t,r,i,k,a){m[i]=m[i]||function(){(m[i].a=m[i].a||[]).push(arguments)};"
        "m[i].l=1*new Date();k=e.createElement(t),a=e.getElementsByTagName(t)[0],k.async=1,"
        "k.src=r,a.parentNode.insertBefore(k,a)})"
        '(window,document,"script","https://mc.yandex.ru/metrika/tag.js","ym");\n'
        f'ym({YM_ID},"init",{{clickmap:true,trackLinks:true,accurateTrackBounce:true}});\n'
        "</script>"
    )


def _api_base_snippet() -> str:
    # Всегда задаём переменную явно, чтобы форма знала адрес бэкенда (или его отсутствие).
    return f'<script>window.__API_BASE="{API_BASE}";</script>'


def build(out_dir: str) -> None:
    if os.path.exists(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir)

    # картинки
    shutil.copytree(os.path.join(WEB, "assets"), os.path.join(out_dir, "assets"))

    index = open(os.path.join(WEB, "index.html"), encoding="utf-8").read()
    privacy = open(os.path.join(WEB, "privacy.html"), encoding="utf-8").read()
    cookie = open(os.path.join(WEB, "cookie.html"), encoding="utf-8").read()

    # --- index.html ---
    index = index.replace("<!--YM_SNIPPET-->", _ym_snippet() + "\n" + _api_base_snippet())
    index = index.replace('src="/assets/', 'src="assets/')
    index = index.replace('href="/privacy#consent"', 'href="privacy.html#consent"')
    index = index.replace('href="/privacy"', 'href="privacy.html"')
    index = index.replace('href="/cookie"', 'href="cookie.html"')

    # --- privacy.html ---
    privacy = privacy.replace('href="/cookie"', 'href="cookie.html"')
    privacy = privacy.replace('href="/"', 'href="index.html"')

    # --- cookie.html ---
    cookie = cookie.replace('href="/privacy"', 'href="privacy.html"')
    cookie = cookie.replace('href="/"', 'href="index.html"')

    open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8").write(index)
    open(os.path.join(out_dir, "privacy.html"), "w", encoding="utf-8").write(privacy)
    open(os.path.join(out_dir, "cookie.html"), "w", encoding="utf-8").write(cookie)

    # GitHub Pages: не прогонять через Jekyll
    open(os.path.join(out_dir, ".nojekyll"), "w").close()

    print(f"Static site built in {out_dir} (API_BASE={API_BASE!r}, YM_ID={YM_ID or '—'})")


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "_site")
    build(out)
