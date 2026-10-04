"""Admin-only commands: inspect who was asked what, and everything users submitted."""

import io
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from siavos_scribe import texts
from siavos_scribe.bot import _app
from siavos_scribe.db import Database, User
from siavos_scribe.intervals import format_interval
from siavos_scribe.pages import PROOFREAD_TRIGGER

DEFAULT_LIMIT = 15
MAX_LIMIT = 100
TG_LIMIT = 4000  # Telegram caps messages at 4096 chars
SNIPPET = 220


# --- formatting (pure, unit-tested) ---

def fmt_time(ts: int | None) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "—"


def snippet(text: str | None, n: int = SNIPPET) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def who(username: str | None, user_id: int) -> str:
    return f"@{username} ({user_id})" if username else str(user_id)


def fmt_duration(seconds: int | None) -> str:
    return f" {seconds // 60}:{seconds % 60:02d}" if seconds else ""


def format_users(rows: list[sqlite3.Row], whitelist: frozenset[int]) -> str:
    registered = {r["user_id"] for r in rows}
    lines = []
    for r in rows:
        flags = []
        if r["user_id"] not in whitelist:
            flags.append("НЕ в whitelist")
        if r["paused"]:
            flags.append("пауза")
        lines.append(
            f"{who(r['username'], r['user_id'])}{' [' + ', '.join(flags) + ']' if flags else ''}\n"
            f"  раз в {format_interval(r['interval_seconds'])} · задано {r['asked']} · "
            f"сообщений {r['saved']} · последнее {fmt_time(r['last_message_at'])}"
        )
    for uid in sorted(whitelist - registered):
        lines.append(f"{uid} [/start ещё не нажат]")
    return "\n".join(lines) or texts.ADMIN_NO_USERS


def format_questions(rows: list[sqlite3.Row], with_user: bool) -> str:
    lines = []
    for r in rows:
        head = f"Q#{r['id']} · {fmt_time(r['asked_at'])} · {r['trigger']} · ответов: {r['answers']}"
        if with_user:
            head += f" · user {r['user_id']}"
        crumb = " › ".join(p for p in (r["section"], r["subsection"]) if p)
        lines.append(f"{head}\n  [{crumb}] {snippet(r['item_text'])}")
    return "\n\n".join(lines) or texts.ADMIN_NOTHING


def format_messages(rows: list[sqlite3.Row], with_user: bool) -> str:
    lines = []
    for r in rows:
        head = f"#{r['id']} · {fmt_time(r['created_at'])} · {r['kind']}{fmt_duration(r['duration'])}"
        if with_user:
            head += f" · user {r['user_id']}"
        body = [head]
        if r["text"]:
            body.append(f"  {snippet(r['text'])}")
        if r["file_path"]:
            body.append(f"  файл: {r['file_path']} (/file {r['id']})")
        if r["question_id"] is not None:
            if r["question_trigger"] == PROOFREAD_TRIGGER:
                body.append(f"  ↳ вычитка «{r['question_text']}» ({r['question_section']}) [Q#{r['question_id']}]")
            else:
                body.append(f"  ↳ Q#{r['question_id']}: {snippet(r['question_text'], 100)}")
        lines.append("\n".join(body))
    return "\n\n".join(lines) or texts.ADMIN_NOTHING


def chunk(text: str, limit: int = TG_LIMIT) -> list[str]:
    """Split on blank lines (then newlines) so each part fits in one Telegram message."""
    parts, cur = [], ""
    for block in text.split("\n\n"):
        while len(block) > limit:  # a single oversized block: hard split
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(block[:limit])
            block = block[limit:]
        if len(cur) + len(block) + 2 > limit and cur:
            parts.append(cur)
            cur = block
        else:
            cur = f"{cur}\n\n{block}" if cur else block
    if cur:
        parts.append(cur)
    return parts


# --- handlers ---

async def _reply_long(update: Update, text: str) -> None:
    for part in chunk(text):
        await update.message.reply_text(part)


def _parse_args(db: Database, args: list[str], *, user_required: bool) -> tuple[User | None, int, str | None]:
    """Returns (user, limit, error). Accepts: [<user>] [N] — N may stand alone."""
    user, limit = None, DEFAULT_LIMIT
    rest = list(args)
    if rest:
        user = db.find_user(rest[0])
        if user is not None:
            rest = rest[1:]
        elif not (rest[0].isdecimal() and len(rest) == 1 and not user_required):
            return None, limit, texts.ADMIN_UNKNOWN_USER.format(ref=rest[0])
    elif user_required:
        return None, limit, texts.ADMIN_NEED_USER
    if rest:
        if not rest[0].isdecimal() or int(rest[0]) < 1:
            return None, limit, texts.ADMIN_BAD_LIMIT
        limit = min(int(rest[0]), MAX_LIMIT)
    return user, limit, None


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(texts.ADMIN_HELP)


async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    app = _app(context)
    await _reply_long(update, format_users(app.db.list_users(), app.cfg.whitelist))


async def cmd_asked(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    app = _app(context)
    user, limit, err = _parse_args(app.db, context.args or [], user_required=False)
    if err:
        await update.message.reply_text(err)
        return
    rows = app.db.recent_questions(user.user_id if user else None, limit)
    title = f"Заданные вопросы{' — ' + who(user.username, user.user_id) if user else ''} (показано: {len(rows)}):\n\n"
    await _reply_long(update, title + format_questions(rows, with_user=user is None))


async def cmd_answers(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    app = _app(context)
    user, limit, err = _parse_args(app.db, context.args or [], user_required=False)
    if err:
        await update.message.reply_text(err)
        return
    rows = app.db.recent_messages(user.user_id if user else None, limit)
    title = f"Сообщения{' — ' + who(user.username, user.user_id) if user else ''} (показано: {len(rows)}):\n\n"
    await _reply_long(update, title + format_messages(rows, with_user=user is None))


async def cmd_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    app = _app(context)
    arg = (context.args or [""])[0]
    if not arg.isdecimal():
        await update.message.reply_text(texts.ADMIN_FILE_USAGE)
        return
    row = app.db.message_by_id(int(arg))
    if row is None or not row["file_path"]:
        await update.message.reply_text(texts.ADMIN_NO_FILE)
        return
    path = (app.cfg.data_dir / row["file_path"]).resolve()
    if not path.is_relative_to(app.cfg.data_dir.resolve()) or not path.is_file():
        await update.message.reply_text(texts.ADMIN_NO_FILE)
        return
    with path.open("rb") as f:
        await update.message.reply_document(f, filename=Path(row["file_path"]).name)


async def cmd_export(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    app = _app(context)
    user = None
    if context.args:
        user = app.db.find_user(context.args[0])
        if user is None:
            await update.message.reply_text(texts.ADMIN_UNKNOWN_USER.format(ref=context.args[0]))
            return
    data = app.db.export(user.user_id if user else None)
    blob = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    name = f"export_{user.user_id if user else 'all'}_{datetime.now():%Y%m%d-%H%M%S}.json"
    await update.message.reply_document(io.BytesIO(blob), filename=name)
