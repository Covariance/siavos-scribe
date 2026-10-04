"""Parse and format human intervals like "12h", "3d", "1d12h", "2ч"."""

import re

_UNIT_SECONDS = {
    "m": 60, "м": 60,
    "h": 3600, "ч": 3600,
    "d": 86400, "д": 86400,
}
_PART = re.compile(r"(\d+)\s*([mhdмчд])")
MAX_INTERVAL = 365 * 86400  # keeps timestamps far from SQLite's 64-bit integer limit


def parse_interval(text: str) -> int | None:
    """Return seconds, or None if `text` isn't a valid interval."""
    s = text.strip().lower().replace(" ", "")
    if not s:
        return None
    parts = _PART.findall(s)
    if not parts or "".join(n + u for n, u in parts) != s:
        return None
    total = sum(int(n) * _UNIT_SECONDS[u] for n, u in parts)
    return total or None


def plural(n: int, one: str, few: str, many: str) -> str:
    """Russian noun form for n: 1 день, 2 дня, 5 дней (11–14 take the "many" form)."""
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def format_interval(seconds: int) -> str:
    """Russian, accusative (always used after "раз в" / "через"): 90000 -> "1 день 1 час", 60 -> "1 минуту"."""
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    parts = []
    if days:
        parts.append(f"{days} {plural(days, 'день', 'дня', 'дней')}")
    if hours:
        parts.append(f"{hours} {plural(hours, 'час', 'часа', 'часов')}")
    if minutes or not parts:
        parts.append(f"{minutes} {plural(minutes, 'минуту', 'минуты', 'минут')}")
    return " ".join(parts)
