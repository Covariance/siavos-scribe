import random

import pytest

from siavos_scribe.db import Database
from siavos_scribe.questions import pick_question
from siavos_scribe.todo_parser import Question


@pytest.fixture
def db():
    d = Database(":memory:")
    d.upsert_user(1, 100, "u", 3600)
    yield d
    d.close()


def pool(n):
    return [Question(f"id{i}", f"text{i}", "S", "") for i in range(n)]


def ask(db, user_id, p, trigger="manual"):
    pick = pick_question(db, user_id, p, random.Random(0))
    q = pick.question
    db.record_question(user_id, q.id, q.text, q.section, q.subsection, pick.cycle, trigger, 1)
    return pick


def test_no_repeats_until_exhausted_then_new_cycle(db):
    p = pool(3)
    first = [ask(db, 1, p) for _ in range(3)]
    assert {x.question.id for x in first} == {"id0", "id1", "id2"}
    assert not any(x.restarted for x in first)
    again = ask(db, 1, p)
    assert again.restarted and again.cycle == 1
    assert db.get_user(1).cycle == 1


def test_users_are_independent(db):
    db.upsert_user(2, 200, "v", 3600)
    p = pool(2)
    ask(db, 1, p)
    ask(db, 1, p)
    pick = ask(db, 2, p)
    assert not pick.restarted


def test_empty_pool(db):
    assert pick_question(db, 1, []) is None


def test_new_items_join_current_cycle(db):
    p = pool(2)
    ask(db, 1, p)
    ask(db, 1, p)
    p = p + [Question("new", "new", "S", "")]
    pick = ask(db, 1, p)
    assert pick.question.id == "new" and not pick.restarted


def test_due_and_pause(db):
    assert db.due_users(0) == []
    assert [u.user_id for u in db.due_users(10**12)] == [1]
    db.set_paused(1, True)
    assert db.due_users(10**12) == []
    db.set_paused(1, False, now=1000)
    assert db.get_user(1).next_due_at == 1000 + 3600
    db.set_paused(1, False, now=5000)  # /resume while not paused keeps the countdown
    assert db.get_user(1).next_due_at == 1000 + 3600


def test_set_interval_resets_timer(db):
    db.set_interval(1, 60, now=500)
    u = db.get_user(1)
    assert (u.interval_seconds, u.next_due_at) == (60, 560)


def test_message_attribution_and_stats(db):
    qid = db.record_question(1, "id0", "t", "S", "", 0, "manual", 77)
    assert db.question_by_tg_message(1, 77) == qid
    assert db.get_user(1).last_question_id == qid
    db.save_message(1, 5, "voice", None, "files/1/x.ogg", 12, qid, 77, "{}")
    assert db.stats(1) == (1, 1)


def test_failed_send_keeps_restart_notice(db):
    p = pool(1)
    ask(db, 1, p)
    unsent = pick_question(db, 1, p, random.Random(0))  # pick, but the send fails: nothing recorded
    assert unsent.restarted and db.get_user(1).cycle == 0
    retry = ask(db, 1, p)
    assert retry.restarted and retry.cycle == 1 and db.get_user(1).cycle == 1


def test_restart_timer_uses_current_interval(db):
    db.set_interval(1, 60, now=0)
    db.restart_timer(1, now=1000)
    assert db.get_user(1).next_due_at == 1060
