"""Load config.toml (settings) and .env (secrets)."""

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from siavos_scribe.intervals import parse_interval


DEFAULT_PROOFREAD_FOLDERS = ("characters", "factions", "items", "locations", "lore", "threads")


@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_id: int
    whitelist: frozenset[int]
    todo_path: Path
    data_dir: Path
    default_interval: int
    min_interval: int
    skip_sections: tuple[re.Pattern[str], ...]
    wiki_dir: Path
    proofread_folders: tuple[str, ...]

    @property
    def db_path(self) -> Path:
        return self.data_dir / "scribe.db"

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "files"


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def _interval(raw: dict, key: str, default: str) -> int:
    value = parse_interval(str(raw.get(key, default)))
    if value is None:
        raise SystemExit(f"config.toml: invalid interval for {key!r} (examples: 30m, 12h, 1d).")
    return value


def load_config(config_path: Path = Path("config.toml"), env_path: Path = Path(".env")) -> Config:
    _load_dotenv(env_path)
    if not config_path.exists():
        raise SystemExit(f"{config_path} not found. Copy config.example.toml to {config_path} and edit it.")
    raw = tomllib.loads(config_path.read_text(encoding="utf-8-sig"))
    if "todo_path" not in raw:
        raise SystemExit(f"todo_path is not set in {config_path} (path to the vault's wiki/TODO.md).")
    todo_path = Path(raw["todo_path"]).expanduser()
    return Config(
        bot_token=os.environ.get("BOT_TOKEN", ""),
        admin_id=int(raw["admin_id"]) if "admin_id" in raw else 0,
        whitelist=frozenset(int(x) for x in raw.get("whitelist", [])),
        todo_path=todo_path,
        data_dir=Path(raw.get("data_dir", "data")).expanduser(),
        default_interval=_interval(raw, "default_interval", "1d"),
        min_interval=_interval(raw, "min_interval", "1h"),
        skip_sections=tuple(re.compile(p) for p in raw.get("skip_sections", [])),
        wiki_dir=Path(raw["wiki_dir"]).expanduser() if "wiki_dir" in raw else todo_path.parent,
        proofread_folders=tuple(raw.get("proofread_folders", DEFAULT_PROOFREAD_FOLDERS)),
    )
