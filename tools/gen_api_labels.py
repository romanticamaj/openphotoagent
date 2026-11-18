"""Copy the zh-TW locale (source of truth: packages/i18n, also read by the web build) into opagent_api as package
data, so the installed app has it (ADR-0023). ``--check`` fails when the copy is out of date (``just lint``)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "packages" / "i18n" / "locales" / "zh-TW.json"
OUT = ROOT / "apps" / "api" / "src" / "opagent_api" / "_data" / "zh-TW.json"


def main(argv: list[str]) -> int:
    want = SRC.read_bytes()
    if "--check" in argv:
        if not OUT.is_file() or OUT.read_bytes() != want:
            print(f"{OUT.relative_to(ROOT)} is out of date: run tools/gen_api_labels.py")
            return 1
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(want)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
