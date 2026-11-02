"""Build the offline gazetteer extract (08 §4.1 GeoNames data; opagent_geo.Gazetteer.load format).

    python tools/build_geonames.py <cities1000.zip> <alternateNamesV2.zip> <admin1CodesASCII.txt> <out.tsv>

Inputs come from https://download.geonames.org/export/dump/ (CC BY 4.0 — credit GeoNames in the app's about page).
Keeps every city with population ≥ 1,000 and its zh-TW / zh / ja / en alternate names. The cloud dev session cannot
reach geonames.org; run this on a machine or CI job that can, and ship the result as the GeoNames data pack.
"""

from __future__ import annotations

import csv
import io
import sys
import zipfile
from pathlib import Path

LANGS = {"zh-TW", "zh-Hant", "zh", "ja", "en"}


def _rows(zpath: Path, member: str) -> csv.reader:  # type: ignore[type-arg]
    z = zipfile.ZipFile(zpath)
    return csv.reader(io.TextIOWrapper(z.open(member), encoding="utf-8"), delimiter="\t", quoting=csv.QUOTE_NONE)


def main(cities: Path, alternates: Path, admin1: Path, out: Path) -> int:
    admin_names = {}
    for line in admin1.read_text(encoding="utf-8").splitlines():
        code, name, *_ = line.split("\t")
        admin_names[code] = name
    places: dict[str, list[str]] = {}
    meta: dict[str, tuple[str, str, str, int]] = {}
    for r in _rows(cities, "cities1000.txt"):
        gid, name, cc, a1, pop = r[0], r[1], r[8], r[10], int(r[14] or 0)
        meta[gid] = (name, cc, admin_names.get(f"{cc}.{a1}", a1), pop)
        places[gid] = [name]
    for r in _rows(alternates, "alternateNamesV2.txt"):
        gid, lang, alt = r[1], r[2], r[3]
        if gid in places and lang in LANGS and alt and "," not in alt and "\t" not in alt:
            places[gid].append(alt)
    with out.open("w", encoding="utf-8") as fh:
        fh.write("# GeoNames cities1000 extract (CC BY 4.0) — built by tools/build_geonames.py\n")
        for gid, (name, cc, a1, pop) in sorted(meta.items(), key=lambda kv: -kv[1][3]):
            names = ",".join(dict.fromkeys(places[gid]))
            fh.write(f"{gid}\t{name}\t{cc}\t{a1}\t{pop}\t{names}\n")
    print(f"wrote {len(meta)} places to {out}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 5:
        raise SystemExit(__doc__)
    raise SystemExit(main(*(Path(a) for a in sys.argv[1:])))
