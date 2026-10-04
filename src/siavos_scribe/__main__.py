"""Entry point: run the bot, or dry-run the TODO parser."""

import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

from siavos_scribe.config import Config, load_config
from siavos_scribe.todo_parser import parse_todo


def _list_questions(cfg: Config) -> None:
    try:
        questions = parse_todo(cfg.todo_path, cfg.skip_sections)
    except (OSError, ValueError) as e:  # ValueError: not valid UTF-8
        raise SystemExit(f"cannot read {cfg.todo_path}: {e}")
    print(f"{len(questions)} open questions in {cfg.todo_path}")
    for section, n in Counter(q.section for q in questions).items():
        print(f"  {n:4d}  {section}")
    print("\nSamples:")
    step = max(1, len(questions) // 5)
    for q in questions[::step][:5]:
        print(f"\n[{q.id}] {q.breadcrumb}\n{q.text}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="siavos-scribe")
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    parser.add_argument("--list-questions", action="store_true", help="parse TODO.md, print stats, exit")
    args = parser.parse_args()

    cfg = load_config(args.config)
    if args.list_questions:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        _list_questions(cfg)
        return

    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)  # its URLs contain the bot token

    from siavos_scribe.bot import ALLOWED_UPDATES, build_application

    build_application(cfg).run_polling(allowed_updates=ALLOWED_UPDATES)


if __name__ == "__main__":
    main()
