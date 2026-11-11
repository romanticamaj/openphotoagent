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

# how a question is worded, not what it asks about: the words around the subject in 「我有幾張滑雪的照片？」
QUESTION_FRAME = re.compile(
    # 「拍了什麼」 「有哪些」 ask about what is in the range: the question, not a word to find (SRCH-PLAN-025)
    r"拍了些?什麼|拍了哪些|拍了啥|有哪些|有什麼|是什麼|"
    r"我有|我的|有沒有|有幾張|有多少張?|多少張|幾張|幫我找|幫我|請列出|請問|請|列出|找出|所有|全部|"
    r"的?照片|的?相片|的?圖片|how many|do i have|show me|find me|find all|[？?！!。，,]+",
    re.IGNORECASE,
)

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
    # 「2025年」 takes its 年 even before a month: 「2025年11月」 left 「年」 behind as a word (SRCH-PLAN-023)
    m = t.take(r"(?<!\d)(1[89]\d\d|20\d\d|2100)(?:\s*年|(?!\d))")
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
    # a whole date, 「2025年11月10日」 「2025/11/10」: that day (SRCH-PLAN-023)
    m = t.take(r"(?<!\d)(1[89]\d\d|20\d\d)\s*[年/.-]\s*(1[0-2]|0?[1-9])\s*[月/.-]\s*(3[01]|[12]\d|0?[1-9])\s*[日號]?")
    if m:
        try:
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            d = None
        if d is not None:
            explain.append(f"「{m.group(0).strip()}」→ {_iso(d)}")
            return _range(d, d + timedelta(days=1))
    n_explain = len(explain)
    year = _year(t, today, explain)
    # 「去年今天」 「去年這個時候」: around today's date in that year (SRCH-PLAN-024)
    for words, days in ((("今天", "今日", "這天"), 3), (("這個時候", "這時候", "此時", "同一時間"), 15)):
        if year is not None and any(t.take_word(w) for w in words):
            try:
                mid = today.replace(year=year)
            except ValueError:  # 29 February
                mid = today.replace(year=year, day=28)
            del explain[n_explain:]
            lo, hi = mid - timedelta(days=days), mid + timedelta(days=days)
            explain.append(f"「{year} 年的今天前後 {days} 天」→ {_iso(lo)} – {_iso(hi)}")
            return _range(lo, hi + timedelta(days=1))
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
    # 「11/10」 with no year: that day in every year, like 「11月10日」 (SRCH-PLAN-027)
    m = t.take(r"(?<![\d/])(1[0-2]|0?[1-9])/(3[01]|[12]\d|0?[1-9])(?![\d/])")
    if m and year is None:
        mo, dd = int(m.group(1)), int(m.group(2))
        try:
            date(2024, mo, dd)
        except ValueError:
            return None
        del explain[n_explain:]
        explain.append(f"「{m.group(0)}」→ 每年的 {mo:02d}-{dd:02d}")
        return {"month_day": f"{mo:02d}-{dd:02d}"}
    # a month and a day, 「11月10日」: that day of the named year, or of every year when none is named, as Google
    # Photos reads it (SRCH-PLAN-023, SRCH-PLAN-026)
    m = t.take(r"(?<!\d)(1[0-2]|0?[1-9])\s*月\s*(3[01]|[12]\d|0?[1-9])\s*[日號]")
    if m:
        mo, dd = int(m.group(1)), int(m.group(2))
        try:
            date(2024, mo, dd)  # a leap year: 2月29日 is a day
        except ValueError:
            return None
        del explain[n_explain:]
        if year is None:
            explain.append(f"「{m.group(0).strip()}」→ 每年的 {mo:02d}-{dd:02d}")
            return {"month_day": f"{mo:02d}-{dd:02d}"}
        try:
            d = date(year, mo, dd)
        except ValueError:
            return None
        explain.append(f"「{m.group(0).strip()}」→ {_iso(d)}")
        return _range(d, d + timedelta(days=1))
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
    free = t.s.lower()  # what no longer word has claimed yet: 海 inside 海邊 is not the sea
    for word in sorted(vocabulary, key=len, reverse=True):
        if word and word in free and vocabulary[word] not in found:
            if word.isascii() and not re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", free):
                continue
            found.append(vocabulary[word])  # the word stays in text too: full text (R3) still sees it (05 §2.3)
            explain.append(f"「{word}」→ 標籤 {vocabulary[word]}")
        if word and word in free:
            free = free.replace(word, " " * len(word))
    return found


def _persons(t: _Text, persons: dict[str, str], scopes: set[str] | None, explain: list[str]) -> list[str]:
    if not persons or (scopes is not None and "faces:read" not in scopes):
        return []  # without faces:read names stay plain text (SRCH-PLAN-010)
    out: list[str] = []
    for alias in sorted(persons, key=len, reverse=True):
        if alias.casefold() in t.s.casefold() and persons[alias] not in out:  # 測試a finds 測試A
            t.take_word(alias)
            out.append(persons[alias])
            explain.append(f"「{alias}」→ 人物")  # the chip shows the name, never an id
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
        t = _Text(QUESTION_FRAME.sub(" ", q or ""))
        explain: list[str] = []
        filters: dict[str, Any] = {}
        people = _persons(t, persons or {}, scopes, explain)
        span = _dates(t, today, tzname, explain)
        if span and "month_day" in span:
            filters["month_day"] = span["month_day"]  # every year's (SRCH-PLAN-026)
        elif span:
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
        if not filters and not text:  # only stop words: searching for nothing would list every photo
            text = unicodedata.normalize("NFKC", q or "").strip()
        confidence = 0.0 if not filters else round(min(1.0, 0.5 + 0.15 * len(filters) - 0.05 * len(text.split())), 2)
        return {
            "text": text,
            "filters": filters,
            "planner": {"source": "rules", "confidence": max(confidence, 0.0), "explain": explain},
        }
    except Exception:
        return {"text": q or "", "filters": {}, "planner": {"source": "rules", "confidence": 0.0, "explain": []}}
