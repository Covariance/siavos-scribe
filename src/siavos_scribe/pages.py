"""Wiki pages offered for proof-reading: everything in the vault except session pages (read-only)."""

import logging
import random
import re
from dataclasses import dataclass
from pathlib import Path

from siavos_scribe.db import Database

log = logging.getLogger(__name__)

PROOFREAD_TRIGGER = "proofread"
ID_PREFIX = "page:"
MIN_BODY_CHARS = 100  # skip stubs; nothing worth proof-reading

_FRONTMATTER = re.compile(r"\A---\r?\n.*?\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)


@dataclass(frozen=True)
class Page:
    id: str  # stable key stored as questions_asked.item_id
    folder: str
    title: str
    body: str  # markdown without the YAML frontmatter


def strip_frontmatter(text: str) -> str:
    return _FRONTMATTER.sub("", text, count=1).strip()


def load_page(wiki_dir: Path, path: Path) -> Page:
    rel = path.relative_to(wiki_dir)
    body = strip_frontmatter(path.read_text(encoding="utf-8-sig"))
    return Page(f"{ID_PREFIX}{rel.as_posix()}", rel.parts[0], path.stem, body)


def list_page_paths(wiki_dir: Path, folders: tuple[str, ...]) -> list[Path]:
    return sorted(p for f in folders for p in (wiki_dir / f).glob("*.md"))


def pick_page(
    db: Database, user_id: int, wiki_dir: Path, folders: tuple[str, ...], rng: random.Random | None = None
) -> Page | None:
    """A random page this user has never proof-read; once all are done, the least recently sent one."""
    rng = rng or random
    last_sent = db.last_proofread(user_id)  # item_id -> id of the latest questions_asked row
    ranked: list[tuple[int, Path]] = []
    for path in list_page_paths(wiki_dir, folders):
        item_id = f"{ID_PREFIX}{path.relative_to(wiki_dir).as_posix()}"
        ranked.append((last_sent.get(item_id, 0), path))
    rng.shuffle(ranked)  # random tie-break among equally (un)seen pages
    for _, path in sorted(ranked, key=lambda r: r[0]):
        try:
            page = load_page(wiki_dir, path)
        except (OSError, ValueError):  # ValueError: not valid UTF-8; one bad file must not block the rest
            log.warning("skipping unreadable wiki page %s", path, exc_info=True)
            continue
        if len(page.body) >= MIN_BODY_CHARS:
            return page
    return None
