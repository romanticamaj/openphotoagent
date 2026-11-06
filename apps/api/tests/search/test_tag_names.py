"""A vocabulary id is for the machine: people see 小孩, not people.child (IMP-022)."""

import pytest
from opagent_api.search import tag_names


@pytest.mark.unit
@pytest.mark.tc("IMP-022")
def test_imp_022_vocabulary_ids_get_their_name_in_the_ui_language():
    assert tag_names(["people.child", "scene.indoor"], "zh-TW") == {"people.child": "小孩", "scene.indoor": "室內"}
    assert tag_names(["people.child"], "en") == {"people.child": "child"}


@pytest.mark.unit
@pytest.mark.tc("IMP-022")
def test_imp_022_a_free_tag_is_already_a_name():
    assert tag_names(["籃球場"], "zh-TW") == {}
    assert tag_names(["made.up"], "zh-TW") == {}
