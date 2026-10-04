import random

import pytest

from siavos_scribe.db import Database
from siavos_scribe.pages import MIN_BODY_CHARS, PROOFREAD_TRIGGER, load_page, pick_page, strip_frontmatter

FOLDERS = ("characters", "locations")
BODY = "# Title\n\n" + "Достаточно длинный текст страницы. " * 5


def test_strip_frontmatter():
    assert strip_frontmatter("---\ntype: npc\naliases: []\n---\n\n# A\n\ntext\n") == "# A\n\ntext"
    assert strip_frontmatter("# A\n\n---\nnot frontmatter\n---\n") == "# A\n\n---\nnot frontmatter\n---"
    assert strip_frontmatter("---\r\ntype: npc\r\n---\r\n# A") == "# A"


@pytest.fixture
def wiki(tmp_path):
    for folder, name in [("characters", "Адарил"), ("characters", "Саул"), ("locations", "Аклинг")]:
        d = tmp_path / folder
        d.mkdir(exist_ok=True)
        (d / f"{name}.md").write_text(f"---\ntype: x\n---\n{BODY}", encoding="utf-8")
    (tmp_path / "characters" / "Заглушка.md").write_text("---\ntype: x\n---\n# Заглушка\n", encoding="utf-8")
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    (sessions / "2024-10-20 Сессия.md").write_text(BODY, encoding="utf-8")
    (tmp_path / "TODO.md").write_text(BODY, encoding="utf-8")
    return tmp_path


@pytest.fixture
def db():
    d = Database(":memory:")
    d.upsert_user(1, 100, "u", 3600)
    d.upsert_user(2, 200, "v", 3600)
    yield d
    d.close()


def proofread(db, user_id, wiki, rng):
    page = pick_page(db, user_id, wiki, FOLDERS, rng)
    db.record_question(user_id, page.id, page.title, page.folder, "", 0, PROOFREAD_TRIGGER, 1)
    return page


def test_load_page(wiki):
    page = load_page(wiki, wiki / "characters" / "Адарил.md")
    assert (page.id, page.folder, page.title) == ("page:characters/Адарил.md", "characters", "Адарил")
    assert page.body == BODY.strip()


def test_no_repeats_until_all_seen_and_no_sessions_or_stubs(wiki, db):
    rng = random.Random(0)
    seen = [proofread(db, 1, wiki, rng).title for _ in range(2)]
    assert sorted(seen + [proofread(db, 1, wiki, rng).title]) == ["Адарил", "Аклинг", "Саул"]
    # everything seen once: the least recently sent comes back first
    assert proofread(db, 1, wiki, rng).title == seen[0]


def test_users_are_independent(wiki, db):
    rng = random.Random(0)
    for _ in range(3):
        proofread(db, 1, wiki, rng)
    assert pick_page(db, 2, wiki, FOLDERS, rng).title in {"Адарил", "Аклинг", "Саул"}
    assert len(db.last_proofread(2)) == 0


def test_regular_questions_do_not_count_as_seen(wiki, db):
    page = pick_page(db, 1, wiki, FOLDERS, random.Random(0))
    db.record_question(1, page.id, page.title, "S", "", 0, "manual", 1)
    assert db.last_proofread(1) == {}


def test_missing_folder_and_all_stubs(tmp_path, db):
    assert pick_page(db, 1, tmp_path, FOLDERS) is None
    (tmp_path / "characters").mkdir()
    (tmp_path / "characters" / "a.md").write_text("x" * (MIN_BODY_CHARS - 1), encoding="utf-8")
    assert pick_page(db, 1, tmp_path, FOLDERS) is None


def test_unreadable_page_is_skipped(wiki):
    (wiki / "locations" / "Битая.md").write_bytes(b"\xff\xfe not utf-8 " * 20)
    db = Database(":memory:")
    db.upsert_user(1, 1, None, 3600)
    seen = {pick_page(db, 1, wiki, FOLDERS, random.Random(seed)).title for seed in range(30)}
    assert "Битая" not in seen and seen


def test_frontmatter_stripped_despite_bom(tmp_path):
    (tmp_path / "lore").mkdir()
    page = tmp_path / "lore" / "Мир.md"
    page.write_bytes(f"---\ntype: x\n---\n{BODY}".encode("utf-8-sig"))
    assert load_page(tmp_path, page).body == BODY.strip()
