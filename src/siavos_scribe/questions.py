"""Choose the next question for a user: random, no repeats within a cycle."""

import random
from dataclasses import dataclass

from siavos_scribe.db import Database
from siavos_scribe.todo_parser import Question


@dataclass(frozen=True)
class Pick:
    question: Question
    cycle: int
    restarted: bool  # pool was exhausted and a new cycle began


def pick_question(
    db: Database, user_id: int, pool: list[Question], rng: random.Random | None = None
) -> Pick | None:
    """Return a question, or None if the pool is empty."""
    if not pool:
        return None
    rng = rng or random
    user = db.get_user(user_id)
    cycle = user.cycle if user else 0
    asked = db.asked_item_ids(user_id, cycle)
    candidates = [q for q in pool if q.id not in asked]
    restarted = not candidates
    if restarted:
        # Not stored yet: record_question advances the user's cycle once the question is really sent,
        # so a failed send repeats the "new round" notice instead of silently losing it.
        cycle += 1
        candidates = pool
    return Pick(rng.choice(candidates), cycle, restarted)
