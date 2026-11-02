"""Offline gazetteer (05 §1.2 `place`, §2.3): place and country names → GeoNames-like records.

The product reads the GeoNames extract shipped with the app (08 §4.1; built by ``tools/build_geonames.py``); tests
use the small ``data/fixture.tsv``. Ambiguous names resolve to the most populous place, and callers can tell the
user how many others share the name (05 §2.3, SRCH-PLAN-006).
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from pathlib import Path

DATA = Path(__file__).with_name("data")

# country names users type (zh-TW / ja / en) → ISO 3166-1 alpha-2
COUNTRIES: dict[str, str] = {
    "日本": "JP",
    "japan": "JP",
    "日本國": "JP",
    "台灣": "TW",
    "臺灣": "TW",
    "taiwan": "TW",
    "韓國": "KR",
    "南韓": "KR",
    "korea": "KR",
    "south korea": "KR",
    "中國": "CN",
    "china": "CN",
    "香港": "HK",
    "hong kong": "HK",
    "美國": "US",
    "usa": "US",
    "united states": "US",
    "英國": "GB",
    "uk": "GB",
    "united kingdom": "GB",
    "法國": "FR",
    "france": "FR",
    "德國": "DE",
    "germany": "DE",
    "義大利": "IT",
    "意大利": "IT",
    "italy": "IT",
    "泰國": "TH",
    "thailand": "TH",
    "越南": "VN",
    "vietnam": "VN",
    "新加坡": "SG",
    "singapore": "SG",
    "澳洲": "AU",
    "australia": "AU",
    "加拿大": "CA",
    "canada": "CA",
}


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKC", s).strip().lower()


@dataclass(frozen=True)
class Place:
    geoname_id: int
    name: str
    country_code: str
    admin1: str
    population: int


class Gazetteer:
    def __init__(self, places: list[tuple[Place, list[str]]]) -> None:
        self._by_name: dict[str, list[Place]] = {}
        for place, names in places:
            for n in {_norm(x) for x in [*names, place.name]}:
                self._by_name.setdefault(n, []).append(place)
        for lst in self._by_name.values():
            lst.sort(key=lambda p: (-p.population, p.geoname_id))

    @classmethod
    def load(cls, path: Path) -> Gazetteer:
        rows: list[tuple[Place, list[str]]] = []
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            gid, name, cc, admin1, pop, names = line.split("\t")
            rows.append((Place(int(gid), name, cc, admin1, int(pop)), names.split(",")))
        return cls(rows)

    @classmethod
    def fixture(cls) -> Gazetteer:
        return cls.load(DATA / "fixture.tsv")

    def lookup(self, text: str) -> list[Place]:
        """Places with this exact name (any language), most populous first."""
        return list(self._by_name.get(_norm(text), []))

    def country(self, text: str) -> str | None:
        return COUNTRIES.get(_norm(text))

    def names(self) -> list[str]:
        """Every known place and country name, longest first (for scanning free text)."""
        return sorted(set(self._by_name) | set(COUNTRIES), key=lambda n: (-len(n), n))
