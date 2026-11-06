"""TCS (ADR-0021): the committed coverage file is checked for consistency with CI's evidence, not byte-equality.

Byte-equality could never pass (回歸 TCS-001): the committed file legitimately carries nightly, visual and perf
evidence PR CI never produces, and CI sees OS- and PG 17-only results the dev box cannot."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tc_status as T

CASES = [
    T.Case("AAA-001", "PR test", "0.1", "U", ["1"], False, "09a"),
    T.Case("AAA-002", "nightly test", "0.1", "N", ["1"], False, "09a"),
    T.Case("AAA-003", "visual test", "0.1", "V", ["2"], False, "09a"),
    T.Case("AAA-004", "not written yet", "0.1", "U", ["2"], False, "09a"),
]
PASS = [{"nodeid": "t::x", "outcome": "passed"}]
FAIL = [{"nodeid": "t::x", "outcome": "failed"}]
INV = {
    "AAA-001": [{"nodeid": "t::a", "tier": "pr"}],
    "AAA-002": [{"nodeid": "t::b", "tier": "nightly"}],
    "AAA-003": [{"nodeid": "e2e::c", "tier": "visual"}],
}


def committed(statuses: dict[str, str], counts: dict[str, int] | None = None) -> str:
    return T.render_status(CASES, statuses, counts or {k: 1 for k, v in statuses.items() if v != "未開始"})


LOCAL = {"AAA-001": "綠", "AAA-002": "綠", "AAA-003": "綠", "AAA-004": "未開始"}


@pytest.mark.contract
@pytest.mark.tc("TCS-001")
def test_tcs_001_local_superset_with_ci_subset_verifies():
    errors, warnings = T.verify(CASES, committed(LOCAL), {"AAA-001": PASS}, INV)
    assert errors == [] and warnings == []


@pytest.mark.contract
@pytest.mark.tc("TCS-002")
@pytest.mark.parametrize("claim", ["綠", "部分", "未開始"])
def test_tcs_002_a_red_ci_result_cannot_be_hidden(claim):
    errors, _ = T.verify(CASES, committed({**LOCAL, "AAA-001": claim}), {"AAA-001": FAIL}, INV)
    assert any("AAA-001" in e for e in errors), errors
    partial = [*PASS, *FAIL]
    errors, _ = T.verify(CASES, committed({**LOCAL, "AAA-001": "綠"}), {"AAA-001": partial}, INV)
    assert any("AAA-001" in e for e in errors), errors


@pytest.mark.contract
@pytest.mark.tc("TCS-003")
def test_tcs_003_green_needs_a_test_ci_could_not_have_run():
    errors, _ = T.verify(CASES, committed({**LOCAL, "AAA-004": "綠"}), {"AAA-001": PASS}, INV)
    assert any("AAA-004" in e for e in errors), errors  # no test at all
    errors, _ = T.verify(CASES, committed(LOCAL), {}, INV)
    assert any("AAA-001" in e for e in errors), errors  # a PR-tier test CI did not report


@pytest.mark.contract
@pytest.mark.tc("TCS-004")
def test_tcs_004_rows_and_summary_must_render_from_the_spec():
    text = committed(LOCAL)
    assert T.verify(CASES, text.replace("| v0.1 | 4 | 3 |", "| v0.1 | 4 | 4 |"), {"AAA-001": PASS}, INV)[0]
    assert T.verify(CASES, text.replace("nightly test", "nightly tset"), {"AAA-001": PASS}, INV)[0]
    assert T.verify(CASES[:3], text, {"AAA-001": PASS}, INV)[0]  # ID set drifted from 09a/09b


@pytest.mark.contract
@pytest.mark.tc("TCS-005")
def test_tcs_005_under_claims_warn_and_nightly_evidence_is_strict_only_when_asked():
    errors, warnings = T.verify(CASES, committed({**LOCAL, "AAA-001": "未開始"}), {"AAA-001": PASS}, INV)
    assert errors == [] and any("AAA-001" in w for w in warnings)
    nightly = {"AAA-002": FAIL}
    errors, warnings = T.verify(CASES, committed(LOCAL), {"AAA-001": PASS}, INV, nightly=nightly)
    assert errors == [] and any("AAA-002" in w for w in warnings)
    errors, _ = T.verify(CASES, committed(LOCAL), {"AAA-001": PASS}, INV, nightly=nightly, strict_nightly=True)
    assert any("AAA-002" in e for e in errors)


@pytest.mark.contract
@pytest.mark.tc("TCS-006")
def test_tcs_006_unknown_ids_fail():
    errors, _ = T.verify(CASES, committed(LOCAL), {"AAA-001": PASS, "ZZZ-999": PASS}, INV)
    assert any("ZZZ-999" in e for e in errors)
    errors, _ = T.verify(CASES, committed(LOCAL), {"AAA-001": PASS}, {**INV, "ZZZ-998": INV["AAA-001"]})
    assert any("ZZZ-998" in e for e in errors)
