import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from siavos_scribe import bot, texts
from siavos_scribe.config import Config
from siavos_scribe.db import Database
from siavos_scribe.todo_parser import TodoCache


class FakeBot:
    def __init__(self):
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id, text, **_):
        self.sent.append((chat_id, text))
        return SimpleNamespace(message_id=len(self.sent))


@pytest.fixture
def ctx(tmp_path: Path):
    todo = tmp_path / "TODO.md"
    todo.write_text("## Раздел\n- [ ] Вопрос один\n- [ ] Вопрос два\n", encoding="utf-8")
    cfg = Config("t", 1, frozenset({7}), todo, tmp_path, 86400, 3600, (), tmp_path, ())
    db = Database(":memory:")
    db.upsert_user(7, 700, "p", cfg.default_interval)
    application = SimpleNamespace(bot_data={"ctx": bot.AppContext(cfg, db, TodoCache(todo))})
    yield SimpleNamespace(application=application, bot=FakeBot(), user_data={})
    db.close()


def questions_sent(context) -> list[str]:
    return [t for _, t in context.bot.sent if "Вопрос" in t]


def test_off_button_is_offered():
    rows = bot._interval_markup(3600).inline_keyboard
    assert rows[-1][0].callback_data == f"iv:{bot.OFF_CALLBACK}"
    assert bot._interval_markup(10**9).inline_keyboard == ((rows[-1][0],),)  # even when no preset fits


@pytest.mark.parametrize("raw", [bot.OFF_CALLBACK, "нет", " Никогда "])
def test_off_during_onboarding_sends_no_question(ctx, raw):
    ctx.user_data.update(awaiting_interval=True, onboarding=True)
    assert asyncio.run(bot._apply_interval(ctx, 7, 700, raw))
    db = ctx.application.bot_data["ctx"].db
    assert db.get_user(7).paused
    assert ctx.bot.sent == [(700, texts.INTERVAL_OFF)]
    assert not ctx.user_data["awaiting_interval"] and "onboarding" not in ctx.user_data


def test_scheduler_never_writes_to_off_users(ctx):
    asyncio.run(bot._apply_interval(ctx, 7, 700, bot.OFF_CALLBACK))
    db = ctx.application.bot_data["ctx"].db
    db.conn.execute("UPDATE users SET next_due_at=0")  # long overdue
    ctx.bot.sent.clear()
    asyncio.run(bot.tick(ctx))
    assert ctx.bot.sent == []


def test_choosing_an_interval_turns_schedule_back_on(ctx):
    asyncio.run(bot._apply_interval(ctx, 7, 700, bot.OFF_CALLBACK))
    asyncio.run(bot._apply_interval(ctx, 7, 700, "12h"))
    db = ctx.application.bot_data["ctx"].db
    assert not db.get_user(7).paused
    db.conn.execute("UPDATE users SET next_due_at=0")
    asyncio.run(bot.tick(ctx))
    assert len(questions_sent(ctx)) == 1


def test_interval_during_onboarding_sends_first_question(ctx):
    ctx.user_data.update(awaiting_interval=True, onboarding=True)
    asyncio.run(bot._apply_interval(ctx, 7, 700, "1d"))
    assert len(questions_sent(ctx)) == 1
