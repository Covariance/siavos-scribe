import re

from siavos_scribe.todo_parser import TodoCache, parse_todo

SAMPLE = """---
type: todo
---

# TODO

- [ ] before any section, must be ignored

## 1. Главное (отмечено Пашей)
- [ ] **Пророчество карты Шут.** Текст, который [[Юнити]] получила из [[Карты|карт]]. → [[Поиск Вернела]]
- [x] ~~Фамилия~~ — **Тафери**

## 2. Акт I — Дигас
### Начало кампании
- [ ] Как началась кампания?
- [ ] Многострочный пункт
  продолжение на второй строке
  - [ ] вложенный пункт теряется
- [ ] Последний

### Конец Акта II (отложено по просьбе Паши)
- [ ] Отложенный вопрос

## 12. Открыто в игре (ответит сама игра)
- [ ] Ещё не случилось

## 13. Сделано
- [x] ~~что-то~~
"""

SKIP = (re.compile("Открыто в игре"), re.compile("Сделано"), re.compile("отложено"))


def _write(tmp_path, text=SAMPLE):
    p = tmp_path / "TODO.md"
    p.write_text(text, encoding="utf-8")
    return p


def test_parses_open_items_only(tmp_path):
    qs = parse_todo(_write(tmp_path), SKIP)
    assert [q.text for q in qs] == [
        "Пророчество карты Шут. Текст, который Юнити получила из карт. → Поиск Вернела",
        "Как началась кампания?",
        "Многострочный пункт продолжение на второй строке",
        "Последний",
    ]


def test_headings_and_breadcrumb(tmp_path):
    qs = parse_todo(_write(tmp_path), SKIP)
    assert qs[0].section == "Главное (отмечено Пашей)"
    assert qs[0].subsection == ""
    assert qs[0].breadcrumb == "Главное (отмечено Пашей)"
    assert qs[1].section == "Акт I — Дигас"
    assert qs[1].breadcrumb == "Акт I — Дигас › Начало кампании"


def test_no_skip_includes_everything_open(tmp_path):
    qs = parse_todo(_write(tmp_path), ())
    assert len(qs) == 6  # +deferred +"Ещё не случилось"


def test_ids_stable_and_unique(tmp_path):
    p = _write(tmp_path)
    a, b = parse_todo(p, SKIP), parse_todo(p, SKIP)
    assert [q.id for q in a] == [q.id for q in b]
    assert len({q.id for q in a}) == len(a)


def test_cache_reparses_on_change(tmp_path):
    p = _write(tmp_path)
    cache = TodoCache(p, SKIP)
    assert len(cache.get()) == 4
    p.write_text(SAMPLE.replace("- [ ] Последний", "- [x] Последний"), encoding="utf-8")
    import os

    st = p.stat()
    os.utime(p, (st.st_atime, st.st_mtime + 5))
    assert len(cache.get()) == 3


def test_utf8_bom_is_ignored(tmp_path):
    f = tmp_path / "TODO.md"
    f.write_bytes("## Раздел\n- [ ] Вопрос\n".encode("utf-8-sig"))
    assert [q.section for q in parse_todo(f)] == ["Раздел"]
