"""Telegram bot: handlers and the scheduler tick."""

import io
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from telegram import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.error import TelegramError

from siavos_scribe import texts
from siavos_scribe.config import Config
from siavos_scribe.db import Database, User
from siavos_scribe.intervals import MAX_INTERVAL, format_interval, parse_interval
from siavos_scribe.pages import PROOFREAD_TRIGGER, pick_page
from siavos_scribe.questions import pick_question
from siavos_scribe.todo_parser import TodoCache

log = logging.getLogger(__name__)

TICK_SECONDS = 60
ALLOWED_UPDATES = [Update.MESSAGE, Update.CALLBACK_QUERY]
TG_TEXT_LIMIT = 4096  # max characters in one Telegram text message
INTERVAL_PRESETS = ["6h", "12h", "1d", "3d", "7d"]
OFF_CALLBACK = "off"  # "iv:off": no scheduled questions at all, only on request
REPLY_KEYBOARD = ReplyKeyboardMarkup(
    [[KeyboardButton(texts.ASK_BUTTON), KeyboardButton(texts.PROOFREAD_BUTTON)]],
    resize_keyboard=True,
    is_persistent=True,
)


@dataclass
class AppContext:
    cfg: Config
    db: Database
    todo: TodoCache


def _app(context: ContextTypes.DEFAULT_TYPE) -> AppContext:
    return context.application.bot_data["ctx"]


def _tg_len(text: str) -> int:
    """Length as Telegram counts it: UTF-16 code units (emoji outside the BMP count as 2)."""
    return len(text.encode("utf-16-le")) // 2


# --- sending questions ---

async def send_question(context: ContextTypes.DEFAULT_TYPE, user: User, trigger: str) -> bool:
    """Pick and send a question, record it, and restart the user's timer."""
    app = _app(context)
    try:
        pool = app.todo.get()
    except (OSError, ValueError):  # ValueError: not valid UTF-8
        log.exception("cannot read %s", app.cfg.todo_path)
        await context.bot.send_message(user.chat_id, texts.POOL_ERROR)
        return False
    pick = pick_question(app.db, user.user_id, pool)
    if pick is None:
        await context.bot.send_message(user.chat_id, texts.POOL_EMPTY)
        return False

    q = pick.question
    body = (
        texts.QUESTION.format(breadcrumb=q.breadcrumb, text=q.text)
        if q.breadcrumb
        else texts.QUESTION_NO_BREADCRUMB.format(text=q.text)
    )
    if pick.restarted:
        body = texts.POOL_RESTARTED + body
    markup = InlineKeyboardMarkup([[InlineKeyboardButton(texts.NEXT_BUTTON, callback_data="next")]])
    msg = await context.bot.send_message(user.chat_id, body, reply_markup=markup)
    app.db.record_question(
        user.user_id, q.id, q.text, q.section, q.subsection, pick.cycle, trigger, msg.message_id
    )
    app.db.restart_timer(user.user_id)
    return True


async def send_proofread(context: ContextTypes.DEFAULT_TYPE, user: User) -> bool:
    """Send one wiki page as markdown and ask the user to proof-read it. Leaves the question timer alone."""
    app = _app(context)
    try:
        page = pick_page(app.db, user.user_id, app.cfg.wiki_dir, app.cfg.proofread_folders)
    except (OSError, ValueError):  # ValueError: not valid UTF-8
        log.exception("cannot read wiki pages in %s", app.cfg.wiki_dir)
        await context.bot.send_message(user.chat_id, texts.PROOFREAD_ERROR)
        return False
    if page is None:
        await context.bot.send_message(user.chat_id, texts.PROOFREAD_EMPTY)
        return False

    markup = InlineKeyboardMarkup([[InlineKeyboardButton(texts.NEXT_PAGE_BUTTON, callback_data="proof")]])
    prompt = texts.PROOFREAD_PROMPT.format(title=page.title, folder=page.folder)
    inline = f"{page.body}{texts.PROOFREAD_SEPARATOR}{prompt}"
    if _tg_len(inline) <= TG_TEXT_LIMIT:
        # One message (raw markdown, no parse_mode), so a reply to it is attributed to this page.
        msg = await context.bot.send_message(user.chat_id, inline, reply_markup=markup)
    else:
        msg = await context.bot.send_document(
            user.chat_id,
            io.BytesIO(page.body.encode("utf-8")),
            filename=f"{page.title}.md",
            caption=texts.PROOFREAD_FILE_CAPTION.format(title=page.title, folder=page.folder),
            reply_markup=markup,
        )
    # Recorded like a question so replies are attributed to it and /asked, /answers show it.
    app.db.record_question(
        user.user_id, page.id, page.title, page.folder, "", user.cycle, PROOFREAD_TRIGGER, msg.message_id
    )
    return True


async def tick(context: ContextTypes.DEFAULT_TYPE) -> None:
    app = _app(context)
    now = int(time.time())
    for due in app.db.due_users(now):
        # Re-read: earlier sends in this loop await, and the user may have switched sending off meanwhile.
        user = app.db.get_user(due.user_id)
        if user is None or user.paused or user.user_id not in app.cfg.whitelist:
            continue
        try:
            sent = await send_question(context, user, "scheduled")
        except Exception:
            log.exception("scheduled send to %s failed", user.user_id)
            sent = False
        if not sent:
            app.db.restart_timer(user.user_id, now)


# --- commands ---

def _registered(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> User | None:
    return _app(context).db.get_user(user_id)


async def _require_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> User | None:
    """The sender's stored user, or None after telling them to /start first."""
    user = _registered(context, update.effective_user.id)
    if user is None:
        await update.message.reply_text(texts.NEED_START)
    return user


def _stop_awaiting_interval(context: ContextTypes.DEFAULT_TYPE) -> None:
    """The user moved on: text is no longer read as an interval, and a later /interval won't send a question."""
    context.user_data["awaiting_interval"] = False
    context.user_data.pop("onboarding", None)


def _interval_markup(min_interval: int) -> InlineKeyboardMarkup:
    row = [
        InlineKeyboardButton(format_interval(s), callback_data=f"iv:{p}")
        for p in INTERVAL_PRESETS
        if (s := parse_interval(p)) and s >= min_interval
    ]
    off = [InlineKeyboardButton(texts.INTERVAL_OFF_BUTTON, callback_data=f"iv:{OFF_CALLBACK}")]
    return InlineKeyboardMarkup([row, off] if row else [off])


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    app = _app(context)
    tg_user, chat = update.effective_user, update.effective_chat
    app.db.upsert_user(tg_user.id, chat.id, tg_user.username, app.cfg.default_interval)
    context.user_data["awaiting_interval"] = True
    context.user_data["onboarding"] = True
    await update.message.reply_text(texts.WELCOME, reply_markup=REPLY_KEYBOARD)
    await update.message.reply_text(
        texts.CHOOSE_INTERVAL, reply_markup=_interval_markup(app.cfg.min_interval)
    )


async def _apply_interval(
    context: ContextTypes.DEFAULT_TYPE, user_id: int, chat_id: int, raw: str
) -> bool:
    """Validate and store an interval (or "off"); returns True if applied."""
    app = _app(context)
    if raw.strip().lower() in texts.INTERVAL_OFF_WORDS | {OFF_CALLBACK}:
        # Scheduled sends skip paused users, so this guarantees nothing arrives unprompted.
        # No onboarding question either: they just said they don't want to be written to.
        app.db.set_paused(user_id, True)
        _stop_awaiting_interval(context)
        await context.bot.send_message(chat_id, texts.INTERVAL_OFF, reply_markup=REPLY_KEYBOARD)
        return True
    seconds = parse_interval(raw)
    if seconds is None:
        await context.bot.send_message(chat_id, texts.INTERVAL_BAD)
        return False
    if seconds < app.cfg.min_interval:
        await context.bot.send_message(
            chat_id, texts.INTERVAL_TOO_SHORT.format(min=format_interval(app.cfg.min_interval))
        )
        return False
    if seconds > MAX_INTERVAL:
        await context.bot.send_message(chat_id, texts.INTERVAL_TOO_LONG.format(max=format_interval(MAX_INTERVAL)))
        return False
    app.db.set_interval(user_id, seconds)
    onboarding = context.user_data.get("onboarding", False)
    _stop_awaiting_interval(context)
    await context.bot.send_message(
        chat_id, texts.INTERVAL_SET.format(interval=format_interval(seconds)), reply_markup=REPLY_KEYBOARD
    )
    if onboarding:
        await send_question(context, app.db.get_user(user_id), "manual")
    return True


async def cmd_interval(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    app = _app(context)
    user = await _require_user(update, context)
    if user is None:
        return
    if context.args:
        await _apply_interval(context, user.user_id, user.chat_id, "".join(context.args))
        return
    context.user_data["awaiting_interval"] = True
    current = (
        texts.INTERVAL_CURRENT_OFF
        if user.paused
        else texts.INTERVAL_CURRENT.format(interval=format_interval(user.interval_seconds))
    )
    await update.message.reply_text(current, reply_markup=_interval_markup(app.cfg.min_interval))


async def cmd_ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await _require_user(update, context)
    if user is None:
        return
    _stop_awaiting_interval(context)
    await send_question(context, user, "manual")


async def cmd_proofread(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await _require_user(update, context)
    if user is None:
        return
    _stop_awaiting_interval(context)
    await send_proofread(context, user)


async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await _require_user(update, context)
    if user is None:
        return
    _app(context).db.set_paused(user.user_id, True)
    await update.message.reply_text(texts.PAUSED)


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    app = _app(context)
    user = await _require_user(update, context)
    if user is None:
        return
    if not user.paused:
        await update.message.reply_text(texts.NOT_PAUSED)
        return
    app.db.set_paused(user.user_id, False)
    await update.message.reply_text(texts.RESUMED.format(interval=format_interval(user.interval_seconds)))


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    app = _app(context)
    user = await _require_user(update, context)
    if user is None:
        return
    if user.paused:
        nxt = texts.STATUS_PAUSED
    else:
        delta = user.next_due_at - int(time.time())
        nxt = (
            texts.STATUS_IN.format(delta=format_interval(delta))
            if delta >= TICK_SECONDS
            else texts.STATUS_SOON
        )
    asked, saved = app.db.stats(user.user_id)
    await update.message.reply_text(
        texts.STATUS.format(
            interval=format_interval(user.interval_seconds), next=nxt, asked=asked, saved=saved
        )
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(texts.WELCOME, reply_markup=REPLY_KEYBOARD)


# --- buttons ---

async def _drop_buttons(query: CallbackQuery) -> None:
    """Remove the inline keyboard so a button can't be pressed twice; failure is harmless."""
    try:
        await query.edit_message_reply_markup(None)
    except TelegramError:
        pass


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user = _registered(context, query.from_user.id)
    if user is None or user.user_id not in _app(context).cfg.whitelist:
        return
    if query.data == "next":
        await _drop_buttons(query)
        await send_question(context, user, "manual")
    elif query.data == "proof":
        await _drop_buttons(query)
        await send_proofread(context, user)
    elif query.data.startswith("iv:"):
        if await _apply_interval(context, user.user_id, user.chat_id, query.data[3:]):
            await _drop_buttons(query)


# --- inbound messages ---

def _media(message) -> tuple[str, object | None, int | None]:
    """(kind, file-bearing object or None, duration)."""
    if message.voice:
        return "voice", message.voice, message.voice.duration
    if message.audio:
        return "audio", message.audio, message.audio.duration
    if message.video_note:
        return "video_note", message.video_note, message.video_note.duration
    if message.video:
        return "video", message.video, message.video.duration
    if message.photo:
        return "other", message.photo[-1], None
    if message.document:
        return "other", message.document, None
    return "text", None, None


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    app = _app(context)
    message = update.message
    user = await _require_user(update, context)
    if user is None:
        return

    text = message.text or message.caption
    if text == texts.ASK_BUTTON:
        await cmd_ask(update, context)
        return
    if text == texts.PROOFREAD_BUTTON:
        await cmd_proofread(update, context)
        return
    if context.user_data.get("awaiting_interval") and message.text and not message.reply_to_message:
        # Short or well-formed text is an interval attempt; long text or a reply is a real answer.
        if parse_interval(text) is not None or len(text) <= 15:
            await _apply_interval(context, user.user_id, user.chat_id, text)
            return

    kind, media, duration = _media(message)
    file_path: str | None = None
    download_failed = False
    if media is not None:
        try:
            tg_file = await media.get_file()
            ext = Path(tg_file.file_path or "").suffix or (".ogg" if kind == "voice" else ".bin")
            target = app.cfg.files_dir / str(user.user_id) / (
                f"{datetime.now():%Y%m%d-%H%M%S}_{message.message_id}{ext}"
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            await tg_file.download_to_drive(target)
            file_path = target.relative_to(app.cfg.data_dir).as_posix()  # stored relative to data_dir
        except Exception:
            log.exception("file download failed for user %s message %s", user.user_id, message.message_id)
            download_failed = True

    reply_to = message.reply_to_message.message_id if message.reply_to_message else None
    question_id = app.db.question_by_tg_message(user.user_id, reply_to) if reply_to else None
    if question_id is None:
        question_id = user.last_question_id
    app.db.save_message(
        user.user_id, message.message_id, kind, text, file_path, duration,
        question_id, reply_to, message.to_json(),
    )

    if download_failed:
        await message.reply_text(texts.SAVE_FILE_FAILED)
        return
    try:
        await message.set_reaction("👍")
    except TelegramError:
        await message.reply_text(texts.SAVED)


async def on_stranger(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    log.info("ignored message from non-whitelisted user %s (@%s)", u.id if u else None, u.username if u else None)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("unhandled error", exc_info=context.error)


def build_application(cfg: Config) -> Application:
    if not cfg.bot_token:
        raise SystemExit("BOT_TOKEN is empty. Put it in .env (see .env.example).")
    if not cfg.admin_id:
        raise SystemExit("admin_id is not set in config.toml (your Telegram numeric user ID).")
    if not cfg.whitelist:
        raise SystemExit("whitelist is empty in config.toml; refusing to start an open bot.")

    db = Database(cfg.db_path)

    async def close_db(_: Application) -> None:
        db.close()

    app = Application.builder().token(cfg.bot_token).post_shutdown(close_db).build()
    app.bot_data["ctx"] = AppContext(cfg, db, TodoCache(cfg.todo_path, cfg.skip_sections))

    from siavos_scribe import admin  # imported here: admin.py imports helpers from this module

    # New messages only: handlers use update.message, which is None for edits.
    private_msg = filters.UpdateType.MESSAGE & filters.ChatType.PRIVATE
    players = filters.User(user_id=list(cfg.whitelist))
    admin_user = filters.User(user_id=cfg.admin_id)
    allowed = private_msg & players
    is_admin = private_msg & admin_user
    for name, fn in [
        ("admin", admin.cmd_admin), ("users", admin.cmd_users), ("asked", admin.cmd_asked),
        ("answers", admin.cmd_answers), ("file", admin.cmd_file), ("export", admin.cmd_export),
    ]:
        app.add_handler(CommandHandler(name, fn, filters=is_admin))
    app.add_handler(CommandHandler("start", cmd_start, filters=allowed))
    app.add_handler(CommandHandler("ask", cmd_ask, filters=allowed))
    app.add_handler(CommandHandler("proofread", cmd_proofread, filters=allowed))
    app.add_handler(CommandHandler("interval", cmd_interval, filters=allowed))
    app.add_handler(CommandHandler("pause", cmd_pause, filters=allowed))
    app.add_handler(CommandHandler("resume", cmd_resume, filters=allowed))
    app.add_handler(CommandHandler("status", cmd_status, filters=allowed))
    app.add_handler(CommandHandler("help", cmd_help, filters=allowed))
    app.add_handler(CallbackQueryHandler(on_callback, pattern=r"^(next|proof|iv:.+)$"))
    content = (
        (filters.TEXT & ~filters.COMMAND)
        | filters.VOICE
        | filters.AUDIO
        | filters.VIDEO_NOTE
        | filters.VIDEO
        | filters.PHOTO
        | filters.Document.ALL
    )
    app.add_handler(MessageHandler(allowed & content, on_message))
    app.add_handler(MessageHandler(filters.UpdateType.MESSAGE & ~players & ~admin_user, on_stranger))
    app.add_error_handler(on_error)

    app.job_queue.run_repeating(tick, interval=TICK_SECONDS, first=10)
    return app
