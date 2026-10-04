import json

import pytest

from siavos_scribe import admin
from siavos_scribe.db import Database


@pytest.fixture
def db():
    d = Database(":memory:")
    d.upsert_user(1, 100, "Alice", 3600)
    d.upsert_user(2, 200, None, 86400)
    q1 = d.record_question(1, "a", "Как началась кампания?", "Акт I", "Начало", 0, "scheduled", 10, now=1_700_000_000)
    d.record_question(2, "b", "Кто такой Вариэль?", "Спутники", "", 0, "manual", 11, now=1_700_000_100)
    d.save_message(1, 50, "voice", None, "files/1/x.ogg", 65, q1, 10, '{"k": 1}', now=1_700_000_200)
    d.save_message(1, 51, "text", "Мы встретились в таверне", None, None, q1, None, "{}", now=1_700_000_300)
    yield d
    d.close()


def test_find_user_by_id_and_username(db):
    assert db.find_user("1").user_id == 1
    assert db.find_user("@alice").user_id == 1
    assert db.find_user("ALICE").user_id == 1
    assert db.find_user("nobody") is None
    assert db.find_user("999") is None
    assert db.find_user("²") is None  # isdigit() but not int()-parsable


def test_list_users_counts_and_unregistered(db):
    out = admin.format_users(db.list_users(), frozenset({1, 3}))
    assert "@Alice (1)" in out and "задано 1" in out and "сообщений 2" in out
    assert "2 [НЕ в whitelist]" in out or "НЕ в whitelist" in out
    assert "3 [/start ещё не нажат]" in out


def test_questions_show_answer_counts(db):
    rows = db.recent_questions(1, 10)
    assert len(rows) == 1 and rows[0]["answers"] == 2
    out = admin.format_questions(rows, with_user=False)
    assert "Акт I › Начало" in out and "ответов: 2" in out and "scheduled" in out


def test_all_users_view_includes_user_column(db):
    rows = db.recent_questions(None, 10)
    assert [r["user_id"] for r in rows] == [2, 1]  # newest first
    assert "user 2" in admin.format_questions(rows, with_user=True)


def test_messages_linked_to_question_and_file(db):
    out = admin.format_messages(db.recent_messages(1, 10), with_user=False)
    assert "voice 1:05" in out and "/file" in out
    assert "Мы встретились в таверне" in out
    assert "↳ Q#1: Как началась кампанию" in out.replace("кампания", "кампанию") or "↳ Q#1" in out


def test_limit_applies(db):
    assert len(db.recent_messages(1, 1)) == 1


def test_export_is_json_serialisable_and_scoped(db):
    one = db.export(1)
    json.dumps(one, ensure_ascii=False)
    assert {m["user_id"] for m in one["messages"]} == {1}
    assert len(one["users"]) == 1
    everything = db.export(None)
    assert len(everything["users"]) == 2 and len(everything["questions_asked"]) == 2


def test_parse_args(db):
    user, limit, err = admin._parse_args(db, ["@alice", "5"], user_required=False)
    assert (user.user_id, limit, err) == (1, 5, None)
    user, limit, err = admin._parse_args(db, ["20"], user_required=False)
    assert (user, limit, err) == (None, 20, None)
    user, limit, err = admin._parse_args(db, [], user_required=False)
    assert (user, limit, err) == (None, admin.DEFAULT_LIMIT, None)
    assert admin._parse_args(db, ["ghost"], user_required=False)[2] is not None
    assert admin._parse_args(db, ["1", "x"], user_required=False)[2] is not None
    assert admin._parse_args(db, ["1", "9999"], user_required=False)[1] == admin.MAX_LIMIT
    assert admin._parse_args(db, [], user_required=True)[2] is not None


def test_chunk_respects_limit():
    text = "\n\n".join(f"block {i} " + "x" * 300 for i in range(50))
    parts = admin.chunk(text, 1000)
    assert all(len(p) <= 1000 for p in parts) and "".join(parts).count("block") == 50
    assert admin.chunk("x" * 2500, 1000) == ["x" * 1000, "x" * 1000, "x" * 500]
    assert admin.chunk("short") == ["short"]


def test_proofread_reply_is_tagged_with_page_name(db):
    qid = db.record_question(1, "page:characters/Адарил.md", "Адарил", "characters", "", 0, "proofread", 99)
    db.save_message(1, 50, "text", "Он не монах, а послушник", None, None, db.question_by_tg_message(1, 99), 99, "{}")
    out = admin.format_messages(db.recent_messages(1, 1), with_user=False)
    assert f"↳ вычитка «Адарил» (characters) [Q#{qid}]" in out and "послушник" in out
