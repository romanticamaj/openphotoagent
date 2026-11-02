"""Rule-based query planner (05 §2.3; 09b SRCH-PLAN): natural language → structured search filters.

Output is the REST ``/search`` request shape (and MCP ``search_photos`` arguments)::

    {"text": "...", "filters": {...}, "planner": {"source": "rules", "confidence": 0.0–1.0, "explain": [...]}}

Every recognised span becomes one filter and one ``explain`` line (shown as a removable chip in the UI); what is
left, minus stop words, stays in ``text``. Dates are local dates in the workspace time zone and ranges are
half-open. The planner is deterministic, does no I/O beyond its injected gazetteer and never raises.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from opagent_core.lunar import lunar_new_year

STOP_WORDS = {
    "的",
    "照片",
    "相片",
    "圖片",
    "在",
    "拍的",
    "用",
    "時",
    "時候",
    "photo",
    "photos",
    "pictures",
    "of",
    "in",
    "the",
    "at",
}
LUNAR_NEW_YEAR = ("農曆新年", "舊曆年", "農曆年", "過年", "春節", "新春", "大年")  # ADR-0008, longest first
SEASONS = {"春天": (3, 6), "春季": (3, 6), "夏天": (6, 9), "夏季": (6, 9), "秋天": (9, 12), "秋季": (9, 12)}
WINTER = ("冬天", "冬季")
CAMERAS = {
    "fujifilm": "FUJIFILM",
    "fuji": "FUJIFILM",
    "富士": "FUJIFILM",
    "sony": "SONY",
    "索尼": "SONY",
    "canon": "Canon",
    "佳能": "Canon",
    "nikon": "NIKON CORPORATION",
    "尼康": "NIKON CORPORATION",
    "iphone": "Apple",
    "apple": "Apple",
    "leica": "LEICA CAMERA AG",
    "徠卡": "LEICA CAMERA AG",
    "olympus": "OLYMPUS CORPORATION",
    "panasonic": "Panasonic",
    "ricoh": "RICOH IMAGING COMPANY, LTD.",
}


def _iso(d: date) -> str:
    return d.isoformat()


def _range(a: date, b: date) -> dict[str, str]:
    return {"from": _iso(a), "to": _iso(b)}


def _next_month(y: int, m: int) -> date:
    return date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)


class _Text:
    """The query with recognised spans blanked out."""

    def __init__(self, q: str) -> None:
        self.s = unicodedata.normalize("NFKC", q)

    def take(self, pattern: str, flags: int = 0) -> re.Match[str] | None:
        m = re.search(pattern, self.s, flags)
        if m:
            self.s = self.s[: m.start()] + " " + self.s[m.end() :]
        return m

    def take_word(self, word: str) -> bool:
        i = self.s.lower().find(word.lower())
        if i < 0:
            return False
        self.s = self.s[:i] + " " + self.s[i + len(word) :]
        return True

    def rest(self) -> str:
        out: list[str] = []
        for tok in self.s.split():
            for w in sorted(STOP_WORDS, key=len, reverse=True):  # particles glued to a token: 在、的
                if tok != w and len(w) == 1 and not w.isascii() and (tok.startswith(w) or tok.endswith(w)):
                    tok = tok.removeprefix(w).removesuffix(w)
            if tok and tok.lower() not in STOP_WORDS:
                out.append(tok)
        return " ".join(out)


def _year(t: _Text, today: date, explain: list[str]) -> int | None:
    m = t.take(r"民國\s*(\d{1,3})\s*年")
    if m:
        y = 1911 + int(m.group(1))
        explain.append(f"「{m.group(0).replace(' ', '')}」→ {y} 年")
        return y
    m = t.take(r"(?<!\d)(1[89]\d\d|20\d\d|2100)\s*年?(?!\d)")
    if m:
        return int(m.group(1))
    for word, delta in (("前年", 2), ("去年", 1), ("今年", 0)):
        if t.take_word(word):
            explain.append(f"「{word}」→ {today.year - delta} 年")
            return today.year - delta
    return None


def _lunar_new_year_range(y: int) -> tuple[date, date] | None:
    d = lunar_new_year(y)
    return (d - timedelta(days=4), d + timedelta(days=5)) if d else None


def _dates(t: _Text, today: date, tz: str, explain: list[str]) -> dict[str, str] | None:
    # relative months first (no year needed)
    if t.take_word("上個月") or t.take_word("上月"):
        first = date(today.year, today.month, 1)
        prev = date(first.year - 1, 12, 1) if first.month == 1 else date(first.year, first.month - 1, 1)
        explain.append(f"「上個月」→ {prev:%Y-%m}（{tz}）")
        return _range(prev, first)
    if t.take_word("這個月") or t.take_word("本月"):
        first = date(today.year, today.month, 1)
        explain.append(f"「這個月」→ {first:%Y-%m}")
        return _range(first, _next_month(first.year, first.month))
    n_explain = len(explain)
    year = _year(t, today, explain)
    for word in LUNAR_NEW_YEAR:
        if t.take_word(word):
            y = year
            if y is None:  # the most recent one that has started (ADR-0008)
                y = today.year
                r = _lunar_new_year_range(y)
                if r is None or r[0] > today:
                    y -= 1
            r = _lunar_new_year_range(y)
            del explain[n_explain:]
            if r is None:
                explain.append(f"「{word}」：{y} 年超出農曆對照範圍（1900–2099），未加日期條件")
                return None
            last = r[1] - timedelta(days=1)
            explain.append(f"「{word}」→ {y} 過年：{_iso(r[0])} – {_iso(last)}（除夕前 3 天至初五）")
            return _range(*r)
    if "新年" in t.s and year is None:
        explain.append("「新年」可能是元旦或過年：請寫「元旦」或「過年」")
    if t.take_word("元旦"):
        y = year if year is not None else today.year
        del explain[n_explain:]
        explain.append(f"「元旦」→ {y}-01-01")
        return _range(date(y, 1, 1), date(y, 1, 2))
    for word, (a, b) in SEASONS.items():
        if t.take_word(word):
            y = year if year is not None else today.year
            del explain[n_explain:]
            explain.append(f"「{word}」→ {y}-{a:02d} – {y}-{b - 1:02d}")
            return _range(date(y, a, 1), date(y, b, 1))
    for word in WINTER:
        if t.take_word(word):
            y = year if year is not None else today.year
            del explain[n_explain:]
            explain.append(f"「{word}」→ {y}-12 – {y + 1}-02")
            return _range(date(y, 12, 1), date(y + 1, 3, 1))
    m = t.take(r"(?<!\d)(1[0-2]|0?[1-9])\s*月(?!份?\d)")
    if m:
        mo = int(m.group(1))
        y = year if year is not None else today.year
        del explain[n_explain:]
        explain.append(f"「{m.group(0).strip()}」→ {y}-{mo:02d}")
        return _range(date(y, mo, 1), _next_month(y, mo))
    if year is not None:
        if len(explain) == n_explain:
            explain.append(f"「{year}」→ {year} 年")
        return _range(date(year, 1, 1), date(year + 1, 1, 1))
    return None


def _place(t: _Text, gazetteer: Any, explain: list[str]) -> dict[str, str] | None:
    if gazetteer is None:
        return None
    lowered = t.s.lower()
    for name in gazetteer.names():
        if not name or name not in lowered:
            continue
        if re.fullmatch(r"[a-z ]+", name) and not re.search(rf"(?<![a-z]){re.escape(name)}(?![a-z])", lowered):
            continue  # latin names only as whole words
        t.take_word(name)
        cc = gazetteer.country(name)
        if cc:
            explain.append(f"「{name}」→ 國家 {cc}")
            return {"country_code": cc}
        places = gazetteer.lookup(name)
        if not places:
            continue
        p = places[0]
        more = f"（另有 {len(places) - 1} 個同名地點）" if len(places) > 1 else ""
        explain.append(f"「{name}」→ {p.name}（{p.admin1}, {p.country_code}）{more}")
        return {"city": p.name, "admin1": p.admin1, "country_code": p.country_code}
    return None


def _camera(t: _Text, explain: list[str]) -> str | None:
    lowered = t.s.lower()
    for word in sorted(CAMERAS, key=len, reverse=True):
        if word in lowered and (not word.isascii() or re.search(rf"(?<![a-z]){word}(?![a-z])", lowered)):
            t.take_word(word)
            t.take(r"拍的|拍攝")
            explain.append(f"「{word}」→ 相機 {CAMERAS[word]}")
            return CAMERAS[word]
    return None


def _tags(t: _Text, vocabulary: dict[str, str], explain: list[str]) -> list[str]:
    found: list[str] = []
    for word in sorted(vocabulary, key=len, reverse=True):
        if word and word in t.s.lower() and vocabulary[word] not in found:
            if word.isascii() and not re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", t.s.lower()):
                continue
            found.append(vocabulary[word])  # the word stays in text too: full text (R3) still sees it (05 §2.3)
            explain.append(f"「{word}」→ 標籤 {vocabulary[word]}")
    return found


def _persons(t: _Text, persons: dict[str, str], scopes: set[str] | None, explain: list[str]) -> list[str]:
    if not persons or (scopes is not None and "faces:read" not in scopes):
        return []  # without faces:read names stay plain text (SRCH-PLAN-010)
    out: list[str] = []
    for alias in sorted(persons, key=len, reverse=True):
        if alias in t.s and persons[alias] not in out:
            t.take_word(alias)
            out.append(persons[alias])
            explain.append(f"「{alias}」→ 人物 {persons[alias]}")
    return out


def _default_vocabulary() -> dict[str, str]:
    """Every name of every tag in the shared vocabulary (zh, en, synonyms → canonical id)."""
    import yaml

    from .tokenize import VOCAB

    out: dict[str, str] = {}
    for term in yaml.safe_load(VOCAB.read_text(encoding="utf-8"))["terms"]:
        for w in [term.get("zh"), term.get("en"), *term.get("synonyms", [])]:
            if w:
                out[str(w).lower()] = term["id"]
    return out


_VOCAB: dict[str, str] | None = None


def plan(
    q: str,
    *,
    today: date,
    tz: ZoneInfo | str = "Asia/Taipei",
    gazetteer: Any = None,
    vocabulary: dict[str, str] | None = None,
    persons: dict[str, str] | None = None,
    scopes: set[str] | None = None,
) -> dict[str, Any]:
    """Plan a query. Never raises: anything unexpected leaves the whole query as plain text."""
    global _VOCAB
    tzname = str(tz)
    try:
        if vocabulary is None:
            if _VOCAB is None:
                _VOCAB = _default_vocabulary()
            vocabulary = _VOCAB
        t = _Text(q or "")
        explain: list[str] = []
        filters: dict[str, Any] = {}
        people = _persons(t, persons or {}, scopes, explain)
        span = _dates(t, today, tzname, explain)
        if span:
            filters["taken_at"] = span
        place = _place(t, gazetteer, explain)
        if place:
            filters["place"] = place
        cam = _camera(t, explain)
        if cam:
            filters["camera_make"] = cam
        tags = _tags(t, vocabulary, explain)
        if tags:
            filters["tags_any"] = tags
        if people:
            filters["persons_all"] = people
        text = t.rest()
        confidence = 0.0 if not filters else round(min(1.0, 0.5 + 0.15 * len(filters) - 0.05 * len(text.split())), 2)
        return {
            "text": text,
            "filters": filters,
            "planner": {"source": "rules", "confidence": max(confidence, 0.0), "explain": explain},
        }
    except Exception:
        return {"text": q or "", "filters": {}, "planner": {"source": "rules", "confidence": 0.0, "explain": []}}
