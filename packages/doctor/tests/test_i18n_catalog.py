"""Every error code is fully translated, including its fix buttons (09b §9.3 I18N-011; 06 §4.7)."""

import json
from pathlib import Path

import pytest
from opagent_doctor.codes import CATALOG, problem

ROOT = Path(__file__).resolve().parents[3]
LANGS = ("zh-TW", "en")
ERRORS = {lang: json.loads((ROOT / f"packages/i18n/errors.{lang}.json").read_text()) for lang in LANGS}
LOCALES = {lang: json.loads((ROOT / f"packages/i18n/locales/{lang}.json").read_text()) for lang in LANGS}


def lookup(d, dotted):
    for part in dotted.split("."):
        if not isinstance(d, dict) or part not in d:
            return None
        d = d[part]
    return d


def label(lang, code, fixes, i):
    """The text a fix button shows — the same resolution as the web ProblemCard's ``fixLabel``."""
    f = fixes[i]
    for k in (f"fix.{code}.{f['type']}", f"fix.{f['id']}", f"fix.{f.get('target')}"):
        v = lookup(LOCALES[lang], k)
        if isinstance(v, str) and v.strip():
            return v
    items = [x.strip() for x in str(ERRORS[lang].get(code, {}).get("cta", "")).split(" · ") if x.strip()]
    return items[i] if len(items) == len(fixes) else None


@pytest.mark.tc("I18N-011")
def test_i18n_011_every_code_both_languages():
    assert len(CATALOG) > 100
    missing = []
    for code in sorted(CATALOG):
        for lang in LANGS:
            e = ERRORS[lang].get(code) or {}
            for field in ("title", "message", "cta"):
                if not str(e.get(field, "")).strip():
                    missing.append(f"{lang} {code}.{field}")
        for deployment in ("desktop", "docker"):
            fixes = problem(code, {}, deployment=deployment)["fixes"]
            for i, f in enumerate(fixes):
                for lang in LANGS:
                    if not label(lang, code, fixes, i):
                        missing.append(f"{lang} label of {code}.{f['id']} ({deployment})")
                    if f.get("consequence") and not lookup(LOCALES[lang], f["consequence"]["key"]):
                        missing.append(f"{lang} {f['consequence']['key']} (consequence of {code})")
    assert sorted(set(missing)) == []
