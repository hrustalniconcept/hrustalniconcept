"""FastAPI-приложение: статика лендинга + /api/lead + Telegram-бот на webhook.

Один процесс: uvicorn раздаёт сайт и юр. страницы, принимает заявки и держит
бота на webhook. Запуск: `uvicorn app.main:app`.
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import config, leads, telegram

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("hrustalni.main")

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")


# --------------------------- Рендер страниц ---------------------------
def _ym_snippet() -> str:
    """Сниппет Яндекс.Метрики. Пусто, если YM_ID не задан."""
    if not config.YM_ID:
        return ""
    ym = config.YM_ID
    return (
        f'<script>window.__YM_ID={ym};</script>\n'
        '<script type="text/javascript">\n'
        "(function(m,e,t,r,i,k,a){m[i]=m[i]||function(){(m[i].a=m[i].a||[]).push(arguments)};"
        "m[i].l=1*new Date();k=e.createElement(t),a=e.getElementsByTagName(t)[0],k.async=1,"
        "k.src=r,a.parentNode.insertBefore(k,a)})"
        '(window,document,"script","https://mc.yandex.ru/metrika/tag.js","ym");\n'
        f'ym({ym},"init",{{clickmap:true,trackLinks:true,accurateTrackBounce:true}});\n'
        "</script>\n"
        f'<noscript><div><img src="https://mc.yandex.ru/watch/{ym}" '
        'style="position:absolute;left:-9999px" alt=""/></div></noscript>'
    )


def _load_page(filename: str, inject_ym: bool = False) -> str:
    path = os.path.join(WEB_DIR, filename)
    with open(path, encoding="utf-8") as fh:
        html = fh.read()
    if inject_ym:
        html = html.replace("<!--YM_SNIPPET-->", _ym_snippet())
    return html


# --------------------------- Жизненный цикл ---------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    application = telegram.build_application()
    if application is not None:
        await application.initialize()
        await application.start()
        try:
            await telegram.setup_commands(application)
        except Exception:  # noqa: BLE001
            logger.exception("Не удалось зарегистрировать команды бота")
        # set_webhook — действие, требующее подтверждения по брифу: выполняется
        # только при заданном PUBLIC_BASE_URL и включённом флаге.
        if config.SET_WEBHOOK_ON_STARTUP and config.webhook_url():
            try:
                await telegram.set_webhook(application)
            except Exception:  # noqa: BLE001
                logger.exception("Не удалось установить webhook")
        else:
            logger.info(
                "Webhook не устанавливается на старте "
                "(SET_WEBHOOK_ON_STARTUP=%s, PUBLIC_BASE_URL=%r)",
                config.SET_WEBHOOK_ON_STARTUP,
                config.PUBLIC_BASE_URL,
            )
    try:
        yield
    finally:
        if application is not None:
            await application.stop()
            await application.shutdown()


app = FastAPI(title="Хрустальный — аудит концепции", lifespan=lifespan, docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[config.ALLOWED_ORIGIN],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)

# Статика картинок лендинга
app.mount("/assets", StaticFiles(directory=os.path.join(WEB_DIR, "assets")), name="assets")


# --------------------------- Маршруты страниц ---------------------------
@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse(_load_page("index.html", inject_ym=True))


@app.get("/privacy", response_class=HTMLResponse)
async def privacy() -> HTMLResponse:
    return HTMLResponse(_load_page("privacy.html"))


@app.get("/cookie", response_class=HTMLResponse)
async def cookie() -> HTMLResponse:
    return HTMLResponse(_load_page("cookie.html"))


@app.get("/healthz", response_class=PlainTextResponse)
async def healthz() -> PlainTextResponse:
    return PlainTextResponse("ok")


# --------------------------- Приём заявки ---------------------------
def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else ""


@app.post("/api/lead")
async def api_lead(payload: leads.LeadIn, request: Request) -> JSONResponse:
    ip = _client_ip(request)

    # Honeypot: ботам отвечаем «успехом», но заявку не сохраняем.
    if leads.is_honeypot_tripped(payload):
        logger.info("Honeypot сработал, IP=%s — заявка отброшена", ip)
        return JSONResponse({"ok": True})

    # Rate-limit по IP.
    if not leads.rate_limiter.allow(ip or "unknown"):
        logger.info("Rate-limit для IP=%s", ip)
        return JSONResponse(
            {"ok": False, "error": "Слишком много заявок. Попробуйте позже."},
            status_code=429,
        )

    try:
        lead = leads.validate_lead_input(payload)
    except leads.LeadValidationError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)

    lead.ip = ip
    lead.user_agent = request.headers.get("user-agent", "")

    try:
        await leads.save_lead(lead)
    except Exception:  # noqa: BLE001
        logger.exception("Ошибка сохранения лида")
        return JSONResponse(
            {"ok": False, "error": "Временная ошибка. Попробуйте ещё раз."},
            status_code=502,
        )

    return JSONResponse({"ok": True})


# --------------------------- Webhook Telegram ---------------------------
@app.post("/telegram/{secret}")
async def telegram_webhook(
    secret: str,
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> Response:
    expected = config.TELEGRAM_WEBHOOK_SECRET
    # Проверяем секрет и в пути, и в заголовке (если он задан Telegram).
    if not expected or secret != expected:
        return Response(status_code=403)
    if x_telegram_bot_api_secret_token is not None and x_telegram_bot_api_secret_token != expected:
        return Response(status_code=403)

    application = telegram._app
    if application is None:
        return Response(status_code=503)

    from telegram import Update

    data = await request.json()
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return Response(status_code=200)
