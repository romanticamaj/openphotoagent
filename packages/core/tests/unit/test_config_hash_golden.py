import json
from pathlib import Path

import pytest

from opagent_core.config_hash import cache_key, config_hash

GOLDEN = Path(__file__).resolve().parents[4] / "fixtures/golden/config_hash.json"


@pytest.mark.unit
@pytest.mark.tc("CORE-HASH-011")
def test_core_hash_011_cache_key_triple():
    k = cache_key("a" * 64, "b" * 64, "c" * 64)
    assert k == cache_key("a" * 64, "b" * 64, "c" * 64)
    assert len({k, cache_key("x" * 64, "b" * 64, "c" * 64), cache_key("a" * 64, "x" * 64, "c" * 64),
                cache_key("a" * 64, "b" * 64, "x" * 64)}) == 4
    # no ambiguity from concatenation
    assert cache_key("ab", "c", "d") != cache_key("a", "bc", "d")


@pytest.mark.contract
@pytest.mark.tc("CORE-HASH-012")
def test_core_hash_012_cross_platform_golden():
    data = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert len(data) >= 20
    for case in data:
        assert config_hash(case["config"]) == case["hash"], case["name"]
