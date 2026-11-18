"""FS-027 (回歸): every text read or write names its encoding.

Windows decodes text as the ANSI code page (cp1252) unless told otherwise; CI's Windows runner failed on a golden
file with a CJK character. Ruff's PLW1514 misses `Path` objects it cannot infer (``Path(...).parents[4] / "x"``),
so this walks the AST of every Python file in the repo."""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKIP = ("node_modules", ".venv", "spikes", "/dist/", "storybook-static", "__pycache__")


def _calls_without_encoding(src: str) -> list[int]:
    bad = []
    for n in ast.walk(ast.parse(src)):
        if not isinstance(n, ast.Call):
            continue
        kws = {k.arg for k in n.keywords}
        if "encoding" in kws or None in kws:  # explicit, or **kwargs passed through
            continue
        f = n.func
        if isinstance(f, ast.Attribute) and f.attr in ("read_text", "write_text"):
            # read_text(enc) / write_text(data, enc) positional encoding is fine too
            if len(n.args) >= (1 if f.attr == "read_text" else 2):
                continue
            bad.append(n.lineno)
        elif (isinstance(f, ast.Name) and f.id == "open") or (
            isinstance(f, ast.Attribute) and f.attr == "open" and isinstance(f.value, ast.Name) and f.value.id == "io"
        ):  # other `.open` attributes are tarfile, zipfile, signers, webbrowser … — not text files
            mode = n.args[1] if len(n.args) > 1 else next((k.value for k in n.keywords if k.arg == "mode"), None)
            if len(n.args) > 3:  # open(file, mode, buffering, encoding)
                continue
            if isinstance(mode, ast.Constant) and isinstance(mode.value, str) and "b" in mode.value:
                continue
            if mode is not None and not isinstance(mode, ast.Constant):
                continue  # a computed mode; cannot tell statically
            bad.append(n.lineno)
    return bad


@pytest.mark.contract
@pytest.mark.tc("FS-027")
def test_fs_027_every_text_io_names_its_encoding():
    found = []
    for p in ROOT.rglob("*.py"):
        s = str(p)
        if any(x in s for x in SKIP):
            continue
        try:
            src = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        found += [f"{p.relative_to(ROOT)}:{ln}" for ln in _calls_without_encoding(src)]
    assert found == [], "text I/O without encoding= (Windows would use cp1252):\n" + "\n".join(found)
