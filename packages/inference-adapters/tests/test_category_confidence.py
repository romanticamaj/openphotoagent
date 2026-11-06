"""A model that says "high" 135 times out of 135 is not telling us anything (ADR-0030): read the token instead."""

import math

import pytest
from opagent_inference.fused import category_probability


def tok(t, tops=None):
    return {"token": t, "top_logprobs": [{"token": k, "logprob": v} for k, v in (tops or {}).items()]}


TOKENS = [
    tok('{"caption":"'),
    tok("香蕉"),
    tok('","tags":["'),
    tok("food"),
    tok('"],"category":"'),
    tok("food", {"food": math.log(0.6), "pets": math.log(0.3), "x": math.log(0.1)}),
    tok('"}'),
]
CATS = ["food", "pets", "other"]


@pytest.mark.unit
@pytest.mark.tc("INF-CAS-015")
def test_inf_cas_015_probability_of_the_chosen_category():
    assert category_probability(TOKENS, "food", CATS) == pytest.approx(0.6 / 0.9, abs=1e-3)
    assert category_probability(TOKENS, "pets", CATS) == pytest.approx(0.3 / 0.9, abs=1e-3)


@pytest.mark.unit
@pytest.mark.tc("INF-CAS-015")
def test_inf_cas_015_a_category_split_over_tokens_counts_its_first_token():
    t = [tok('{"category":"'), tok("arch", {"arch": math.log(0.8), "art": math.log(0.2)}), tok("itecture"), tok('"}')]
    assert category_probability(t, "architecture", ["architecture", "art"]) == pytest.approx(0.8, abs=1e-3)


@pytest.mark.unit
@pytest.mark.tc("INF-CAS-015")
def test_inf_cas_015_the_word_category_in_a_caption_is_not_the_field():
    t = [
        tok('{"caption":"the \\"category\\":\\"'),
        tok("pets", {"pets": 0.0}),
        tok('","category":"'),
        tok("food", {"food": 0.0, "pets": -5.0}),
        tok('"}'),
    ]
    assert category_probability(t, "food", ["food", "pets"]) > 0.9


@pytest.mark.unit
@pytest.mark.tc("INF-CAS-016")
def test_inf_cas_016_no_logprobs_means_no_number():
    assert category_probability(None, "food", CATS) is None
    assert category_probability([tok('{"caption":"x"}')], "food", CATS) is None
