"""Parse the vault's wiki/TODO.md into a list of open questions (read-only)."""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

_OPEN_ITEM = re.compile(r"^- \[ \]\s+(.*)$")
_ANY_ITEM = re.compile(r"^\s*- \[[ xX]\]")
_WIKILINK = re.compile(r"\[\[([^\]|]*)(?:\|([^\]]*))?\]\]")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_NUMBER_PREFIX = re.compile(r"^\d+\.\s*")


@dataclass(frozen=True)
class Question:
    id: str
    text: str
    section: str
    subsection: str

    @property
    def breadcrumb(self) -> str:
        return " › ".join(p for p in (self.section, self.subsection) if p)


def clean(text: str) -> str:
    """Strip Obsidian/markdown syntax, collapse whitespace."""
    text = _WIKILINK.sub(lambda m: m.group(2) or m.group(1), text)
    text = _BOLD.sub(r"\1", text)
    text = text.replace("`", "")
    return re.sub(r"\s+", " ", text).strip()


def _heading(text: str) -> str:
    return _NUMBER_PREFIX.sub("", clean(text))


def _make_question(raw: str, section: str, subsection: str) -> Question:
    text = clean(raw)
    item_id = hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
    return Question(item_id, text, section, subsection)


def parse_todo(path: Path, skip_sections: tuple[re.Pattern[str], ...] = ()) -> list[Question]:
    """Return open `- [ ]` items, skipping headings that match `skip_sections`."""
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    questions: list[Question] = []
    section = subsection = ""
    pending: list[str] | None = None  # raw lines of the item being collected

    def flush() -> None:
        nonlocal pending
        if pending is not None:
            skipped = any(p.search(h) for p in skip_sections for h in (section, subsection))
            if not skipped:
                questions.append(_make_question(" ".join(pending), section, subsection))
        pending = None

    for line in lines:
        if line.startswith("## "):
            flush()
            section, subsection = _heading(line[3:]), ""
        elif line.startswith("### "):
            flush()
            subsection = _heading(line[4:])
        elif m := _OPEN_ITEM.match(line):
            flush()
            if section:  # ignore anything before the first "##"
                pending = [m.group(1)]
        elif pending is not None and line.startswith((" ", "\t")) and line.strip() and not _ANY_ITEM.match(line):
            pending.append(line.strip())  # indented continuation
        else:
            flush()
    flush()
    return [q for q in questions if q.text]


class TodoCache:
    """Re-parses the file only when its mtime changes."""

    def __init__(self, path: Path, skip_sections: tuple[re.Pattern[str], ...] = ()):
        self.path = path
        self.skip_sections = skip_sections
        self._mtime: float | None = None
        self._questions: list[Question] = []

    def get(self) -> list[Question]:
        mtime = self.path.stat().st_mtime
        if mtime != self._mtime:
            self._questions = parse_todo(self.path, self.skip_sections)
            self._mtime = mtime
        return self._questions
