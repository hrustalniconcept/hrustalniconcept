"""Telegram-бот: карточки заявок в группу продаж + воронка `/start`.

python-telegram-bot 21.x, режим webhook. Тексты пользовательских сообщений —
строго из брифа (раздел 6), тон бренда: на «вы», без эмодзи и инфобиза.
"""
from __future__ import annotations

import html
import logging
from datetime import datetime, timedelta, timezone

from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from . import config, leads, legal

logger = logging.getLogger("hrustalni.telegram")

# Состояния воронки «Разобрать мой проект»
REGION, STAGE, NAME, PHONE, CONFIRM = range(5)

# Стадии проекта (кнопки шага 2 воронки)
STAGE_OPTIONS = [
    "Только участок/идея",
    "Есть эскиз/мастер-план",
    "В проектировании",
    "В стройке/продажах",
]

_app: Application | None = None


# ============================== ТЕКСТЫ (раздел 6) ==============================
T_START = (
    "Здравствуйте. Это бот Концепт-бюро «Хрустальный».\n\n"
    "Мы — практикующий девелопер, а не консультант. Помогаем увидеть слабые "
    "места проекта коттеджного посёлка до стройки: окупаемость, стратегия, риски.\n\n"
    "Выберите, с чего начать."
)

T_AUDIT = (
    "Аудит концепции — независимый разбор проекта по восьми блокам: локация, "
    "право, инженерия, генплан, продукт, коммерция, эксплуатация, экономика.\n\n"
    "На выходе понятно, окупается ли проект, отвечает ли он стратегии и где "
    "спрятаны риски и резервы капитализации — до того, как это станет дорогой "
    "правкой на площадке."
)

T_CASE = (
    "Обезличенный кейс: первый посёлок команды на Урале, комфорт-класс.\n\n"
    "Аудит показал, что проектные решения опережали стратегию — мастер-план и "
    "дома уже рисовались, а финмодель, продуктовая матрица и правовые риски ещё "
    "нет. Разобрали статус подъездного пути и взгляд банка, гипотезу центральной "
    "воды, ширину дорог, упущенный видовой ресурс и причину покупки.\n\n"
    "Полный обезличенный кейс пришлём по запросу."
)

T_CONTACT = (
    "Концепт-бюро «Хрустальный»\n"
    "Telegram: @HrustalniDevelopment\n"
    "Сайт: hrustalni.com\n"
    "Телефон: +7 (924) 530-73-00\n"
    "Почта: hrustalni.school@gmail.com"
)

T_CASE_REQUESTED = "Передали команде — пришлём."

T_FUNNEL_REGION = "В каком регионе участок?"
T_FUNNEL_STAGE = "На какой стадии проект?"
T_FUNNEL_NAME = "Оставьте имя и телефон — команда свяжется и предложит формат.\n\nКак к вам обращаться?"
T_FUNNEL_PHONE = "Спасибо. Оставьте, пожалуйста, номер телефона для связи."
T_FUNNEL_PHONE_RETRY = "Похоже, номер указан не полностью. Введите телефон в формате +7 999 123-45-67."
T_FUNNEL_CONSENT = (
    "Отправляя данные, вы соглашаетесь с обработкой персональных данных: "
    "https://hrustalni.com/privacy"
)
T_FUNNEL_DONE = (
    "Спасибо. Передали команде — свяжемся в ближайшее время. Обычно в таких "
    "случаях начинают с аудита."
)
T_CANCEL = "Хорошо, остановились. Чтобы начать заново — нажмите /start."


# ============================== Клавиатуры ==============================
def _kb_start() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Что такое аудит", callback_data="info_audit")],
            [InlineKeyboardButton("Обезличенный кейс", callback_data="info_case")],
            [InlineKeyboardButton("Разобрать мой проект", callback_data="start_funnel")],
        ]
    )


def _kb_audit() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Разобрать мой проект", callback_data="start_funnel")],
            [InlineKeyboardButton("Обезличенный кейс", callback_data="info_case")],
        ]
    )


def _kb_case() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Получить полный кейс", callback_data="get_full_case")],
            [InlineKeyboardButton("Разобрать мой проект", callback_data="start_funnel")],
        ]
    )


def _kb_stage() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(opt, callback_data=f"stage:{i}")]
        for i, opt in enumerate(STAGE_OPTIONS)
    ]
    return InlineKeyboardMarkup(rows)


def _kb_consent() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("Отправить", callback_data="submit_lead")]])


def _kb_lead_status(lead_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Взять в работу", callback_data=f"leadstat:take:{lead_id}"),
                InlineKeyboardButton("Квалифицирован", callback_data=f"leadstat:qual:{lead_id}"),
                InlineKeyboardButton("Отклонить", callback_data=f"leadstat:reject:{lead_id}"),
            ]
        ]
    )


# ============================== Карточка лида в группу ==============================
_SOURCE_LABELS = {"site": "сайт", "bot": "бот", "bot-case": "бот — запрос кейса"}


def _fmt_msk(iso_utc: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        msk = dt.astimezone(timezone(timedelta(hours=3)))
        return msk.strftime("%d.%m.%Y %H:%M") + " МСК"
    except ValueError:
        return iso_utc


def _render_card(lead: leads.Lead) -> str:
    e = html.escape
    src = _SOURCE_LABELS.get(lead.source, lead.source)
    lines = [
        "<b>Новая заявка</b>",
        "",
        f"Имя: <b>{e(lead.name) or '—'}</b>",
        f"Телефон: <b>{e(lead.phone) or '—'}</b>",
        f"Регион: {e(lead.region) or '—'}",
        f"Стадия: {e(lead.stage) or '—'}",
    ]
    if lead.comment:
        lines.append(f"Комментарий: {e(lead.comment)}")
    if lead.tg_username:
        lines.append(f"Telegram: @{e(lead.tg_username)}")
    lines += [
        f"Источник: {e(src)}",
        f"Время: {e(_fmt_msk(lead.created_at))}",
        f"Согласие: {e(lead.consent_version)} ({'да' if lead.consent else 'нет'})",
        f"<code>id {e(lead.id)}</code>",
    ]
    return "\n".join(lines)


async def send_lead_card(lead: leads.Lead) -> None:
    """Отправить карточку заявки в группу продаж с кнопками статуса."""
    if _app is None:
        logger.warning("Бот не инициализирован — карточка лида %s не отправлена", lead.id)
        return
    if config.TELEGRAM_SALES_CHAT_ID is None:
        logger.warning("TELEGRAM_SALES_CHAT_ID не задан — карточка лида %s не отправлена", lead.id)
        return
    await _app.bot.send_message(
        chat_id=config.TELEGRAM_SALES_CHAT_ID,
        text=_render_card(lead),
        parse_mode=ParseMode.HTML,
        reply_markup=_kb_lead_status(lead.id),
        disable_web_page_preview=True,
    )


# ============================== Команды ==============================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(T_START, reply_markup=_kb_start())


async def cmd_audit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(T_AUDIT, reply_markup=_kb_audit())


async def cmd_case(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(T_CASE, reply_markup=_kb_case())


async def cmd_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        T_CONTACT, reply_markup=_kb_start(), disable_web_page_preview=True
    )


async def cmd_webhookinfo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Админ-команда: показать статус вебхука. Только для ADMIN_USER_ID."""
    user = update.effective_user
    if not user or config.ADMIN_USER_ID is None or user.id != config.ADMIN_USER_ID:
        return  # тихо игнорируем неадминов
    info = await context.bot.get_webhook_info()
    await update.effective_message.reply_text(
        f"Webhook: {info.url or '—'}\n"
        f"Pending updates: {info.pending_update_count}\n"
        f"Last error: {info.last_error_message or '—'}"
    )


# ============================== Инфо-кнопки ==============================
async def cb_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "info_audit":
        await query.message.reply_text(T_AUDIT, reply_markup=_kb_audit())
    elif data == "info_case":
        await query.message.reply_text(T_CASE, reply_markup=_kb_case())
    elif data == "info_contact":
        await query.message.reply_text(T_CONTACT, disable_web_page_preview=True)
    elif data == "get_full_case":
        await _handle_full_case_request(update, context)


async def _handle_full_case_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """«Получить полный кейс» → лид с пометкой «кейс» + ответ."""
    user = update.effective_user
    lead = leads.Lead(
        name=(user.full_name if user else "").strip(),
        phone="",
        region="",
        stage="",
        source="bot-case",
        consent=False,
        consent_version=legal.CONSENT_VERSION,
        comment="Запрос полного обезличенного кейса",
        tg_username=(user.username or "") if user else "",
    )
    try:
        await leads.save_lead(lead)
    except Exception:  # noqa: BLE001
        logger.exception("Не удалось сохранить запрос кейса")
    await update.callback_query.message.reply_text(T_CASE_REQUESTED)


# ============================== Воронка ==============================
async def funnel_enter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["lead"] = {}
    await query.message.reply_text(T_FUNNEL_REGION)
    return REGION


async def funnel_region(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.setdefault("lead", {})["region"] = update.message.text.strip()
    await update.message.reply_text(T_FUNNEL_STAGE, reply_markup=_kb_stage())
    return STAGE


async def funnel_stage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    try:
        idx = int(query.data.split(":", 1)[1])
        stage_text = STAGE_OPTIONS[idx]
    except (ValueError, IndexError):
        stage_text = ""
    context.user_data.setdefault("lead", {})["stage"] = stage_text
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(T_FUNNEL_NAME)
    return NAME


async def funnel_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()
    if len(name) < 2:
        await update.message.reply_text("Как к вам обращаться? Напишите имя.")
        return NAME
    context.user_data.setdefault("lead", {})["name"] = name
    await update.message.reply_text(T_FUNNEL_PHONE)
    return PHONE


async def funnel_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    phone = leads.normalize_phone_ru(update.message.text)
    if not phone:
        await update.message.reply_text(T_FUNNEL_PHONE_RETRY)
        return PHONE
    context.user_data.setdefault("lead", {})["phone"] = phone
    await update.message.reply_text(T_FUNNEL_CONSENT, reply_markup=_kb_consent(), disable_web_page_preview=True)
    return CONFIRM


async def funnel_submit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data = context.user_data.get("lead", {})
    user = update.effective_user
    lead = leads.Lead(
        name=data.get("name", "").strip(),
        phone=data.get("phone", "").strip(),
        region=data.get("region", "").strip(),
        stage=data.get("stage", "").strip(),
        source="bot",
        consent=True,
        consent_version=legal.CONSENT_VERSION,
        tg_username=(user.username or "") if user else "",
    )
    try:
        await leads.save_lead(lead)
    except Exception:  # noqa: BLE001
        logger.exception("Не удалось сохранить лид из воронки")
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(T_FUNNEL_DONE)
    context.user_data.pop("lead", None)
    return ConversationHandler.END


async def funnel_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("lead", None)
    await update.effective_message.reply_text(T_CANCEL)
    return ConversationHandler.END


# ============================== Статус лида (кнопки в группе) ==============================
_STATUS_LABELS = {"take": "Взято в работу", "qual": "Квалифицирован", "reject": "Отклонено"}


async def cb_lead_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    parts = query.data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    label = _STATUS_LABELS.get(action, action)
    who = query.from_user.full_name if query.from_user else "—"
    await query.answer(label)
    base = query.message.text_html if query.message else ""
    new_text = f"{base}\n\n<b>Статус: {html.escape(label)}</b> — {html.escape(who)}"
    try:
        await query.edit_message_text(
            new_text, parse_mode=ParseMode.HTML, reply_markup=_kb_lead_status_remaining()
        )
    except Exception:  # noqa: BLE001 — например, текст не изменился
        logger.debug("Не удалось обновить карточку статуса", exc_info=True)


def _kb_lead_status_remaining() -> InlineKeyboardMarkup | None:
    # После выставления статуса убираем кнопки — статус зафиксирован в тексте.
    return None


# ============================== Сборка приложения ==============================
def build_application() -> Application | None:
    """Собрать PTB Application. None — если токен не задан (локальный режим без бота)."""
    global _app
    if not config.bot_enabled():
        logger.warning("TELEGRAM_BOT_TOKEN не задан — бот отключён, сайт работает без него")
        return None

    application = Application.builder().token(config.TELEGRAM_BOT_TOKEN).updater(None).build()

    funnel = ConversationHandler(
        entry_points=[CallbackQueryHandler(funnel_enter, pattern=r"^start_funnel$")],
        states={
            REGION: [MessageHandler(filters.TEXT & ~filters.COMMAND, funnel_region)],
            STAGE: [CallbackQueryHandler(funnel_stage, pattern=r"^stage:\d+$")],
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, funnel_name)],
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, funnel_phone)],
            CONFIRM: [CallbackQueryHandler(funnel_submit, pattern=r"^submit_lead$")],
        },
        fallbacks=[
            CommandHandler("cancel", funnel_cancel),
            CommandHandler("start", funnel_cancel),
        ],
        allow_reentry=True,
    )

    application.add_handler(funnel)
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("audit", cmd_audit))
    application.add_handler(CommandHandler("case", cmd_case))
    application.add_handler(CommandHandler("contact", cmd_contact))
    application.add_handler(CommandHandler("webhookinfo", cmd_webhookinfo))
    application.add_handler(
        CallbackQueryHandler(cb_info, pattern=r"^(info_audit|info_case|info_contact|get_full_case)$")
    )
    application.add_handler(CallbackQueryHandler(cb_lead_status, pattern=r"^leadstat:"))

    _app = application
    return application


async def setup_commands(application: Application) -> None:
    """Зарегистрировать список команд (дублирует профиль из @BotFather)."""
    await application.bot.set_my_commands(
        [
            BotCommand("start", "Начать и подобрать формат"),
            BotCommand("audit", "Что такое аудит концепции"),
            BotCommand("case", "Получить обезличенный кейс"),
            BotCommand("contact", "Связаться с командой"),
        ]
    )


async def set_webhook(application: Application) -> bool:
    """Повесить webhook на секретный путь. True — если успешно."""
    url = config.webhook_url()
    if not url:
        logger.warning("PUBLIC_BASE_URL не задан — webhook не установлен")
        return False
    await application.bot.set_webhook(
        url=url,
        secret_token=config.TELEGRAM_WEBHOOK_SECRET or None,
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )
    logger.info("Webhook установлен: %s", url)
    return True
