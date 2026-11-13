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


@pytest.mark.unit
@pytest.mark.tc("SRCH-TOK-010")
def test_srch_tok_010_a_word_also_finds_its_synonyms_in_captions():
    """回歸 (audit round 1): 「海邊」 found none of the captions that say 沙灘 or 海灘."""
    from opagent_search.tokenize import to_tsquery_alts

    pq = Tokenizer().query("海邊")
    assert {"海灘", "沙灘"} <= set(pq.alts["海邊"])
    q = to_tsquery_alts(pq.words, pq.alts)
    assert q.startswith("(") and "'沙灘'" in q and "'海邊'" in q and " | " in q
    assert to_tsquery_alts(["狗", "草地"], {}) == "'狗' & '草地'"


@pytest.mark.unit
@pytest.mark.tc("SRCH-TOK-011")
def test_srch_tok_011_the_vocabulary_covers_a_family_library():
    """回歸 (audit round 1): 22 terms; 小朋友 (217 photos), 教室, 積木 and 大象 had no id."""
    import yaml
    from opagent_core.vocabularies import VOCAB_DIR

    tok = Tokenizer()
    for word, key in [
        ("小朋友", "people.child"),
        ("幼童", "people.child"),
        ("教室", "scene.classroom"),
        ("積木", "object.blocks"),
        ("大象", "animal.elephant"),
        ("畫畫", "activity.drawing"),
    ]:
        assert tok.tag_key(word) == key, word
    seen: dict[str, str] = {}
    for t in yaml.safe_load((VOCAB_DIR / "general-photo.yaml").read_text(encoding="utf-8"))["terms"]:
        assert t["zh"] and t["en"], t["id"]
        for s in [t["zh"], t["en"], *t.get("synonyms", [])]:
            key = normalize(s)
            assert seen.setdefault(key, t["id"]) == t["id"], f"{s} is both {seen[key]} and {t['id']}"


@pytest.mark.unit
@pytest.mark.tc("SRCH-TOK-013")
def test_srch_tok_013_degree_words_are_not_concepts():
    """回歸 (audit round 23): 「小孩踢球很開心」 made 很 a concept every photo had to show."""
    words = [c.word for c in Tokenizer().concepts("小孩踢球很開心")]
    assert "很" not in words and "開心" in words
    assert [c.word for c in Tokenizer().concepts("非常漂亮的夕陽")] == ["漂亮", "夕陽"]


@pytest.mark.unit
@pytest.mark.tc("SRCH-TOK-014")
def test_srch_tok_014_an_activity_may_imply_a_scene():
    """ADR-0032 addendum 2026-11-11 (implies): only activity/object → scene, no chains; 衝浪 is a term now."""
    import yaml
    from opagent_core.vocabularies import VOCAB_DIR

    tok = Tokenizer()
    assert tok.tag_key("衝浪") == "activity.surfing" and tok.tag_key("衝浪板") == "object.surfboard"
    assert tok.implied("activity.surfing") == {"scene.sea", "scene.beach"}
    assert tok.implied("object.surfboard") == set()  # a board on a car roof is not at the sea
    terms = yaml.safe_load((VOCAB_DIR / "general-photo.yaml").read_text(encoding="utf-8"))["terms"]
    ids = {t["id"] for t in terms}
    for t in terms:
        for b in t.get("implies", []):
            assert t["id"].split(".")[0] in ("activity", "object"), t["id"]
            assert b.startswith("scene.") and b in ids, (t["id"], b)
            assert not next(x for x in terms if x["id"] == b).get("implies"), "no chains"


@pytest.mark.unit
@pytest.mark.tc("SRCH-TOK-015")
def test_srch_tok_015_quantity_words_are_not_concepts():
    """回歸 (audit round 42): 「一張有很多彩色方塊的圖」 made 一張 and 很多 things a photo had to show."""
    words = [c.word for c in Tokenizer().concepts("一張有很多彩色方塊的圖")]
    assert "一張" not in words and "很多" not in words and "圖" not in words
    assert "好多" not in [c.word for c in Tokenizer().concepts("好多隻貓")]


@pytest.mark.unit
@pytest.mark.tc("SRCH-TOK-016")
def test_srch_tok_016_measure_words_are_not_concepts():
    """回歸 (audit round 43): 「好多隻貓」 said 「沒有照片拍到「隻」」: a measure word was a search term."""
    for q, want in [("好多隻貓", ["貓"]), ("三個小朋友", ["小朋友"]), ("兩隻狗在草地上", ["狗", "草地"])]:
        got = [c.word for c in Tokenizer().concepts(q)]
        assert not {"隻", "個", "些", "三", "兩"} & set(got), (q, got)
        assert all(w in got for w in want), (q, got)


@pytest.mark.unit
@pytest.mark.tc("SRCH-TOK-017")
def test_srch_tok_017_place_words_are_not_concepts():
    """回歸 (audit round 44): 「兩隻狗在草地上」 said 「沒有照片拍到「上」」: a place word was a search term."""
    for q, want in [
        ("兩隻狗在草地上", ["狗", "草地"]),
        ("箱子裡的貓", ["箱子", "貓"]),
        ("桌子旁邊的小朋友", ["桌子", "小朋友"]),
    ]:
        got = [c.word for c in Tokenizer().concepts(q)]
        assert not {"上", "裡", "旁邊", "旁"} & set(got), (q, got)
        assert all(w in got for w in want), (q, got)


@pytest.mark.unit
@pytest.mark.tc("SRCH-TOK-018")
def test_srch_tok_018_a_street_named_sunset_is_not_a_sunset():
    """回歸 (audit round 50): 「夕陽」 found a highway whose caption read the sign 「日落大道」: a name holding a
    vocabulary word was cut into that word. Names stay whole, so the concept does not match them."""
    tok = Tokenizer()
    doc = tok.document(caption="高速公路橋下車流穿梭，路牌指示前往好萊塢與日落大道。")
    assert "日落" not in doc["B"] and "日落大道" in doc["B"]
    assert "日落" in tok.document(caption="海邊的日落")["B"]  # a real sunset still is one
    assert tok.tag_key("日落") == "mood.sunset"


@pytest.mark.unit
@pytest.mark.tc("SRCH-TOK-019")
def test_srch_tok_019_only_characters_traditional_text_cannot_have_are_converted():
    """Q-030's conversion, kept narrow: whole-text OpenCC also rewrote correct Taiwan text (設備→裝置, 文件→檔案,
    台→臺, 家具→傢俱) in 45 of 2,197 captions. Only characters outside Big5 (cp950) are converted: 2 captions, both
    「开心」."""
    from opagent_search.tokenize import to_hant

    assert to_hant("两个小孩很开心地玩积木") == "兩個小孩很開心地玩積木"
    for keep in ("剪彩、洗手台、家具、設備、文件、自行車、工作台、托盤、划槳", "Two kids play.", "", None):
        assert to_hant(keep) == keep


@pytest.mark.unit
@pytest.mark.tc("SRCH-TOK-020")
def test_srch_tok_020_a_broader_word_includes_its_kinds():
    """回歸 (audit rounds 57-58): 「車子」 was a synonym of 汽車 only, so buses, trains and bicycles were missed though
    their words were in the captions. 車子 is its own term that includes those kinds (strict: every bus is a 車子;
    a plane is not), and 「汽車」 still means a car only. Checked like `implies`: no chains, same group."""
    import yaml
    from opagent_core.vocabularies import VOCAB_DIR

    tok = Tokenizer()
    assert tok.tag_key("車子") == "object.vehicle" and tok.tag_key("汽車") == "object.car"
    forms = {f for c in tok.concepts("車子") for f in c.forms}
    assert {"公車", "巴士", "火車", "捷運", "腳踏車", "汽車"} <= forms and "飛機" not in forms
    assert sorted(tok.query("車子").tag_keys) == [
        "object.bicycle",
        "object.bus",
        "object.car",
        "object.train",
        "object.vehicle",
    ]
    assert tok.query("汽車").tag_keys == ["object.car"]
    assert tok.vector_text("車子") == "vehicle"
    terms = yaml.safe_load((VOCAB_DIR / "general-photo.yaml").read_text(encoding="utf-8"))["terms"]
    by_id = {t["id"]: t for t in terms}
    for t in terms:
        for k in t.get("includes", []):
            assert k in by_id and k != t["id"], (t["id"], k)
            assert not by_id[k].get("includes"), "no chains"
            assert k.split(".")[0] == t["id"].split(".")[0], (t["id"], k)
    assert "飯" not in {f for c in tok.concepts("吃飯") for f in c.forms}  # eating is not a bowl of rice


@pytest.mark.unit
@pytest.mark.tc("SRCH-TOK-021")
@pytest.mark.parametrize(
    ("q", "out"),
    [
        ("slide", "playground"),  # a synonym: the concept's English name, as 溜滑梯 asks
        ("eat", "eating"),
        ("lunch", "eating"),
        ("午餐", "eating"),
        ("鞦韆", "playground"),
        ("吃", "eating"),  # audit round 70: 吃 alone found 0, 吃早餐 lost 吃
        ("宵夜", "eating"),  # audit round 105: meal words that dead-ended on 問 AI
        ("下午茶", "eating"),
        ("便當", "eating"),
        ("點心", "eating"),
        ("brunch", "eating"),
        ("爬山", "hiking"),
        ("彈跳床", "trampoline"),
        ("a puppy on the seaside", "a dog on the beach"),  # the other words of a phrase stay where they are
        ("red car", "red car"),
        ("trampoline", "trampoline"),  # a word the vocabulary does not know: as typed
    ],
)
def test_srch_tok_021_english_synonyms_ask_the_concepts_name(q, out):
    """回歸 (audit round 69): 「slide」 counted 29 and 「遊樂場」 14, 「eat」 1 and 「meal」 7: an English synonym went to
    the image model as typed, while the Chinese word went by the concept's English name. One concept, one question."""
    assert Tokenizer().vector_text(q) == out


@pytest.mark.unit
@pytest.mark.tc("SRCH-TOK-022")
def test_srch_tok_022_words_inside_a_query_the_vocabulary_knows():
    """What a query that finds nothing can try instead (audit round 69): 「爬山」 found nothing while 「山」 had photos.
    The vocabulary's words written inside the query, by their Chinese name, longest first; not the query itself."""
    tok = Tokenizer()
    assert tok.related("爬山") == ["山"]
    assert tok.related("山") == []
    assert tok.related("海邊撿貝殼")[0] == "海灘"  # 海邊 is a name of the beach
    assert tok.related("長頸鹿") == []


@pytest.mark.unit
@pytest.mark.tc("SRCH-TOK-023")
@pytest.mark.parametrize(
    ("q", "out"),
    [
        ("吃蛋糕", "蛋糕"),
        ("吃冰淇淋", "冰淇淋"),
        ("吃香蕉", "香蕉"),  # a word the vocabulary does not know, after 吃, is what is eaten
        ("eat cake", "cake"),
        ("吃飯", "吃飯"),  # nothing else: eating itself
        ("在公園吃東西", "在公園吃東西"),  # beside a place it is a concept of its own
    ],
)
def test_srch_tok_023_eating_a_thing_is_that_thing(q, out):
    """回歸 (audit round 72): 「吃蛋糕」 still found 10 photos and 「蛋糕」 1; 「吃香蕉」 said no photo shows 香蕉, which
    finds 3. Beside only foods or words the vocabulary does not know, 吃 leaves the query: 吃蛋糕 is 蛋糕."""
    assert Tokenizer().without_eating(q).strip() == out


@pytest.mark.unit
@pytest.mark.tc("SRCH-TOK-024")
@pytest.mark.parametrize(
    ("q", "out"),
    [
        ("去爬山", "爬山"),
        ("跳彈跳床", "彈跳床"),
        ("堆沙堡", "沙堡"),
        ("搭飛機", "飛機"),
        ("看海", "海"),
        ("吃蛋糕", "蛋糕"),
        ("去", "去"),  # nothing else: left as typed
        ("跳舞", "跳舞"),  # one word, not a verb before a thing
        ("坐車", "車"),  # audit round 74
        ("公園放風箏", "公園 風箏"),
        ("做蛋糕", "蛋糕"),
    ],
)
def test_srch_tok_024_a_verb_before_a_thing_is_that_thing(q, out):
    """回歸 (audit round 73): 「去爬山」「跳彈跳床」「堆沙堡」 said 「沒有照片拍到「去」」 above what the rest found: the
    verb stood as a concept no photo shows. Like 吃 (SRCH-TOK-023), a light verb before other words leaves the query."""
    assert Tokenizer().without_verbs(q).strip() == out


@pytest.mark.unit
@pytest.mark.tc("SRCH-TOK-025")
def test_srch_tok_025_one_concept_reads_the_same_by_any_of_its_names():
    """回歸 (audit round 77): 「沙灘」 found 4 and 「海邊」 6, one term: the character-pair fallback took the pairs of
    the word as typed only. A known word's pairs are any of its names'. 「球」 found 51 and 「足球」 120: captions say
    足球, 踢球, 籃球, one word each to the segmenter, never 球 alone. 公園 includes 遊樂場, where its slides are."""
    tok = Tokenizer()
    a, b = tok.bigram_query("沙灘"), tok.bigram_query("海邊")
    assert "§沙灘" in a and "§海邊" in a and "|" in a
    assert set(a.replace("(", "").replace(")", "").split(" | ")) == set(
        b.replace("(", "").replace(")", "").split(" | ")
    )
    assert tok.bigram_query("長頸鹿") == to_tsquery(tok.bigrams("長頸鹿"))
    forms = tok.concepts("球")[0].forms
    assert {"足球", "踢球", "籃球", "棒球", "網球"} <= set(forms)
    assert tok.tag_key("足球") == "activity.soccer"  # 足球 still names soccer
    # 公園 including 遊樂場 was measured and withdrawn (audit round 78): 16 word hits made an anchor that counted 44
    # school courts; a park is not where its slides are, to the image model (ADR-0048 addendum 5)
    assert "scene.playground" not in tok.included("scene.park")
    assert tok.words("籃球場") == ["籃球場"] and tok.tag_key("籃球場") == "scene.sports_field"  # not 籃球 + 場
    assert tok.words("籃球架") == ["籃球架"]  # a hoop tag is not basketball being played (audit round 79)


@pytest.mark.unit
@pytest.mark.tc("SRCH-TOK-026")
def test_srch_tok_026_a_christmas_hat_is_a_hat():
    """回歸 (audit round 82): 「聖誕帽」 said no photo shows 「帽」 while 「帽子」 found 2: there was no hat in the
    vocabulary, so 聖誕 and 帽 were two words no photo had."""
    tok = Tokenizer()
    assert tok.words("聖誕帽") == ["聖誕帽"] and tok.tag_key("聖誕帽") == "object.hat"
    assert tok.tag_key("帽子") == "object.hat" and tok.vector_text("聖誕帽") == "hat"


@pytest.mark.unit
@pytest.mark.tc("SRCH-TOK-027")
def test_srch_tok_027_an_event_suggests_what_it_is_seen_by():
    """回歸 (audit round 92): 「生日」 and 「露營」 found nothing and suggested nothing, while 蛋糕 and 帳篷 each had a
    photo. An event the vocabulary knows names the things it is seen by; a query that finds nothing tries those too."""
    tok = Tokenizer()
    assert "蛋糕" in tok.related("生日")
    assert tok.tag_key("露營") == "activity.camping" and "帳篷" in tok.related("露營")
    assert tok.tag_key("帳篷") == "object.tent"


@pytest.mark.unit
@pytest.mark.tc("SRCH-TOK-028")
@pytest.mark.parametrize(
    ("q", "en"),
    [
        ("煙火", "fireworks"),
        ("泡泡", "bubbles"),
        ("烤肉", "barbecue"),
        ("野餐", "picnic"),
        ("沙坑", "sandbox"),
        ("洗澡", "bath"),
        ("跳繩", "jump rope"),
        ("夜市", "night market"),
        ("雪人", "snowman"),
        ("恐龍", "dinosaur"),
    ],
)
def test_srch_tok_028_everyday_family_words_reach_the_image_model(q, en):
    """回歸 (audit round 93): 煙火, 泡泡, 烤肉 and others dead-ended on 問 AI: a Chinese word the vocabulary does not
    know is never asked of the image model (ADR-0031), so not even a look-alike could be found. They are terms now."""
    tok = Tokenizer()
    assert tok.vector_text(q) == en


@pytest.mark.unit
@pytest.mark.tc("SRCH-TOK-029")
def test_srch_tok_029_an_event_inside_a_longer_query_still_suggests():
    """回歸 (audit round 93): 「生日派對」 suggested nothing while 「生日」 suggested 蛋糕."""
    assert "蛋糕" in Tokenizer().related("生日派對")


@pytest.mark.unit
@pytest.mark.tc("SRCH-TOK-030")
def test_srch_tok_030_a_meal_suggests_food():
    """回歸 (audit round 96): 吃飯, eat, 午餐 and lunch counted nothing (eating is carried by no photo) and offered only
    問 AI, while 食物 has photos. A meal is seen by its food."""
    tok = Tokenizer()
    for q in ("吃飯", "午餐", "eat"):
        assert "食物" in tok.related(q) or "食物" in tok.related(tok.term_name(tok.tag_key(q), "zh")), q


@pytest.mark.unit
@pytest.mark.tc("SRCH-TOK-031")
def test_srch_tok_031_a_meal_is_seen_by_its_food_through_tags_only():
    """ADR-0048 addendum 7 (one deliberation sub-agent; audit round 97: 吃飯, eat and 午餐 counted 0 with 16 meal
    photos). `seen_by`: a photo tagged with one of these shows the activity, through the tag route only; never a form
    of the word (SRCH-TOK-020 stands), never the image prompt, never backwards."""
    import yaml
    from opagent_core.vocabularies import VOCAB_DIR

    tok = Tokenizer()
    assert tok.query("吃飯").tag_keys == ["activity.eating", "food.meal", "food.rice"]
    assert "飯" not in {f for c in tok.concepts("吃飯") for f in c.forms}
    assert tok.vector_text("吃飯") == "eating"
    assert tok.query("飯").tag_keys == ["food.rice"] and tok.query("食物").tag_keys == ["food.meal"]
    terms = yaml.safe_load((VOCAB_DIR / "general-photo.yaml").read_text(encoding="utf-8"))["terms"]
    by_id = {t["id"]: t for t in terms}
    for t in terms:
        for k in t.get("seen_by", []):
            assert k in by_id and t["id"].startswith("activity.") and k.split(".")[0] in ("object", "food"), (
                t["id"],
                k,
            )
            assert not by_id[k].get("seen_by") and not by_id[k].get("includes"), "no chains"
            assert k not in (t.get("includes") or [])


@pytest.mark.unit
@pytest.mark.tc("SRCH-TOK-032")
def test_srch_tok_032_a_food_suggests_food():
    """回歸 (audit round 106): 火鍋, 牛排, 拉麵 and 冰淇淋 found nothing and offered no 食物 chip, while 烤肉 did. Every
    food suggests 食物. Revised after round 107: 火鍋 and 牛排 as names of 食物 claimed 16 food photos with no hotpot
    or steak in them; they are foods of their own, which suggest 食物 like the others."""
    tok = Tokenizer()
    for q in ("冰淇淋", "拉麵", "蛋糕", "火鍋", "牛排"):
        assert "食物" in tok.related(q), q
    assert tok.tag_key("火鍋") == "food.hotpot" and tok.tag_key("牛排") == "food.steak"
