"""A question about one photo is asked of the model (audit round 30): the fused caption prompt ignored it, so 「問這張」
answered every question with the photo's caption."""

import asyncio
import json

import pytest
from opagent_adapters.openai_compat import OpenAICompatAdapter
from opagent_inference.types import BatchRequest, ImageInput


def _adapter(reply: str):
    a = OpenAICompatAdapter("http://127.0.0.1:1", structured_output="json_schema")
    a.loaded = True
    sent: list[dict] = []

    async def post(client, body):
        sent.append(body)
        return reply, None

    a._post = post  # type: ignore[method-assign]
    return a, sent


def _img():
    return ImageInput(item_id="a1", content_hash="h1", data=b"\xff\xd8jpeg")


def _text(body) -> str:
    return "\n".join(
        part["text"] if isinstance(part, dict) else str(part)
        for m in body["messages"]
        for part in (m["content"] if isinstance(m["content"], list) else [m["content"]])
        if not isinstance(part, dict) or part.get("type") == "text"
    )


@pytest.mark.unit
@pytest.mark.tc("INF-ASK-001")
def test_inf_ask_001_a_question_is_asked_and_answered_in_words():
    a, sent = _adapter("大約一歲左右，體型還很小。")
    [out] = asyncio.run(
        a.run_batch(BatchRequest(task_type="caption", items=[_img()], variables={"question": "這隻小象大概幾歲？"}))
    )
    assert "這隻小象大概幾歲？" in _text(sent[0])
    assert "response_format" not in sent[0]  # an answer, not the caption JSON
    assert out.caption == "大約一歲左右，體型還很小。"


@pytest.mark.unit
@pytest.mark.tc("INF-ASK-002")
def test_inf_ask_002_a_verify_condition_reaches_the_model():
    reply = json.dumps({"caption": "有小孩在畫畫", "tags": ["child"], "category": "yes", "confidence": "high"})
    a, sent = _adapter(reply)
    [out] = asyncio.run(
        a.run_batch(
            BatchRequest(
                task_type="classify",
                items=[_img()],
                variables={"categories": ["yes", "no", "unsure"], "question": "小孩在畫畫"},
            )
        )
    )
    assert "小孩在畫畫" in _text(sent[0]) and out.category == "yes"


@pytest.mark.unit
@pytest.mark.tc("INF-ASK-001")
def test_inf_ask_001_a_plain_caption_is_unchanged():
    reply = json.dumps({"caption": "一頭小象", "tags": ["elephant"], "category": "other", "confidence": "high"})
    a, sent = _adapter(reply)
    [out] = asyncio.run(a.run_batch(BatchRequest(task_type="caption", items=[_img()])))
    assert "response_format" in sent[0] and out.caption == "一頭小象"
