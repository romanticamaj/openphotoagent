import json
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from opagent_search.tokenize import TOKENIZER_VERSION, Tokenizer, normalize, to_tsquery

ROOT = Path(__file__).resolve().parents[3]
TOK = Tokenizer(custom_words=["柴犬", "新城鄉"])


@pytest.mark.unit
@pytest.mark.tc("SRCH-TOK-001")
def test_srch_tok_001_normalize():
    assert normalize("ＣＡＴ") == "cat" and normalize("Ｃａｔ") == "cat"
    assert normalize("c\u200ba\u200dt\ufeff") == "cat"


@pytest.mark.unit
@pytest.mark.tc("SRCH-TOK-003")
def test_srch_tok_003_custom_words_single_token():
    assert "柴犬" in TOK.words("一隻柴犬在草地上")
    assert "新城鄉" in TOK.words("花蓮縣新城鄉的海邊")


@pytest.mark.unit
@pytest.mark.tc("SRCH-TOK-004")
def test_srch_tok_004_cjk_bigram_fallback():
    d = TOK.document(caption="夕陽海灘")
    assert {"§夕陽", "§陽海", "§海灘"} <= set(d["D"])
    q = TOK.query("夕陽海灘")
    assert q.bigram is not None and "§陽海" in q.bigram and q.words != q.bigram


@pytest.mark.unit
@pytest.mark.tc("SRCH-TOK-006")
@pytest.mark.parametrize("word", ["貓", "cat", "ねこ", "猫"])
def test_srch_tok_006_canonical_tag_key(word):
    assert TOK.tag_key(word) == "animal.cat"
    q = TOK.query(word)
    assert "animal.cat" in q.tag_keys
    d = TOK.document(tags=["貓"])
    assert "animal.cat" in d["A"]


@pytest.mark.contract
@pytest.mark.tc("SRCH-TOK-007")
def test_srch_tok_007_golden():
    golden = json.loads((ROOT / "fixtures/golden/tokenize.json").read_text(encoding="utf-8"))
    assert golden["tokenizer"] == TOKENIZER_VERSION
    assert len(golden["cases"]) == 100
    for case in golden["cases"]:
        assert TOK.words(case["text"]) == case["words"], case["text"]


@pytest.mark.property
@pytest.mark.tc("SRCH-TOK-008")
@settings(max_examples=500, deadline=None)
@given(
    st.text(alphabet=st.sampled_from([*list("&|!():*'\\<>-\" abc貓海邊"), "\x00", "\u200b"]), max_size=40)
    | st.text(max_size=40)
)
def test_srch_tok_008_tsquery_never_breaks(s):
    import re

    q = to_tsquery(TOK.query(s).words)
    assert isinstance(q, str)
    # every lexeme is a quoted literal with quotes/backslashes escaped: the only operators are the ones we add
    lit = re.compile(r"'(?:[^'\\]|''|\\\\)*'")
    assert all(lit.fullmatch(t) for t in q.split(" & ") if t), q


@pytest.mark.unit
@pytest.mark.tc("SRCH-SEM-010")
@pytest.mark.parametrize(
    ("q", "out"),
    [
        ("海邊", "beach"),  # a synonym of scene.beach: its English name, not the synonym list
        ("貓", "cat"),
        ("小孩在玩水", "child"),  # only what maps; CJK words raise every photo's cosine (ADR-0031)
        ("長頸鹿", None),  # nothing maps: the image route stays out, so "nothing found" is possible
        ("red car", "red car"),  # Latin script as it is
        ("夜景", "night"),
    ],
)
def test_srch_sem_010_what_the_image_model_is_asked(q, out):
    """回歸 (audit round 1): SigLIP does not separate Chinese queries (貓: relevant 0.086 best, irrelevant p99 0.076;
    cat: 0.107 vs 0.051), so 「貓」 returned 27 classroom photos out of 29."""
    assert Tokenizer().vector_text(q) == out
