"""ASK-LLM-001: the OpenAI-compatible client for llama-server (``--jinja``) and external endpoints (10 §8.2).

Tool calls and text replies are parsed into ``Reply``; broken tool JSON never becomes a tool call; timeouts are a
quiet empty reply; images are never sent; the tool-calling probe decides whether the general loop may run."""

import json

import httpx
import pytest
from opagent_assistant.openai_compat import OpenAICompatLLM

TOOLS = [
    {
        "name": "count_photos",
        "description": "count",
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}},
    }
]


def server(replies, seen):
    def handle(req: httpx.Request) -> httpx.Response:
        seen.append({"url": str(req.url), "headers": dict(req.headers), "body": json.loads(req.content)})
        r = replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return httpx.Response(200, json=r)

    return httpx.MockTransport(handle)


def tool_reply(name, args):
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{"id": "c1", "type": "function", "function": {"name": name, "arguments": args}}],
                }
            }
        ]
    }


def text_reply(t):
    return {"choices": [{"message": {"role": "assistant", "content": t}}]}


@pytest.mark.contract
@pytest.mark.tc("ASK-LLM-001")
def test_llm_001_tool_calls_and_text():
    seen = []
    llm = OpenAICompatLLM(
        "https://llm.example/v1",
        api_key="sk-test",
        model="qwen3-vl-8b",
        tools=TOOLS,
        transport=server([tool_reply("count_photos", '{"query": "貓"}'), text_reply("共有 3 張")], seen),
    )
    msgs = [{"role": "system", "content": "s", "tools": ["count_photos"]}, {"role": "user", "content": "幾張貓"}]
    r = llm.next(msgs)
    assert (r.tool, r.args, r.text) == ("count_photos", {"query": "貓"}, None)
    r2 = llm.next([*msgs, {"role": "tool", "name": "count_photos", "content": '{"count": 3}'}])
    assert r2.text == "共有 3 張" and r2.tool is None
    body = seen[0]["body"]
    assert seen[0]["url"] == "https://llm.example/v1/chat/completions"
    assert seen[0]["headers"]["authorization"] == "Bearer sk-test"
    assert body["model"] == "qwen3-vl-8b" and body["temperature"] <= 0.3
    assert [t["function"]["name"] for t in body["tools"]] == ["count_photos"]
    assert all("tools" not in m for m in body["messages"])  # our tool list is not leaked into the messages
    tool_msg = seen[1]["body"]["messages"][-1]
    assert tool_msg["role"] == "tool" and tool_msg["tool_call_id"] and tool_msg["content"] == '{"count": 3}'
    assert llm.cloud is True and llm.bytes_out == sum(len(json.dumps(s["body"]).encode()) for s in seen)
    assert '"image' not in json.dumps([s["body"] for s in seen])  # never images (ASK-PRV-002)


@pytest.mark.contract
@pytest.mark.tc("ASK-LLM-001")
def test_llm_001_broken_json_timeout_and_local():
    seen = []
    llm = OpenAICompatLLM(
        "http://127.0.0.1:8080/v1",
        tools=TOOLS,
        transport=server([tool_reply("count_photos", "{not json"), httpx.ReadTimeout("slow"), text_reply("ok")], seen),
    )
    assert llm.cloud is False and "authorization" not in seen
    bad = llm.next([{"role": "user", "content": "x"}])
    assert bad.tool is None and bad.text == "" and llm.last_error == "TOOL_JSON_INVALID"
    slow = llm.next([{"role": "user", "content": "x"}])
    assert slow.tool is None and slow.text == "" and llm.last_error == "TIMEOUT"
    assert llm.next([{"role": "user", "content": "x"}]).text == "ok" and llm.last_error is None
    unknown = OpenAICompatLLM(
        "http://127.0.0.1:8080/v1", tools=TOOLS, transport=server([tool_reply("rm_rf", "{}")], [])
    )
    r = unknown.next([{"role": "user", "content": "x"}])
    assert r.tool is None and unknown.last_error == "TOOL_UNKNOWN"  # only tools we offered


@pytest.mark.contract
@pytest.mark.tc("ASK-LLM-001", "ASK-LOOP-003")
def test_llm_001_probe_decides_the_loop():
    good = OpenAICompatLLM(
        "http://127.0.0.1:8080/v1",
        tools=TOOLS,
        transport=server([tool_reply("count_photos", '{"query": "貓"}')] * 3, []),
    )
    assert good.probe() is True and good.tool_calling_ok is True
    flaky = OpenAICompatLLM(
        "http://127.0.0.1:8080/v1",
        tools=TOOLS,
        transport=server(
            [
                tool_reply("count_photos", '{"query": "貓"}'),
                text_reply("我數了一下，3 張"),
                tool_reply("count_photos", "{}"),
            ],
            [],
        ),
    )
    assert flaky.probe() is False and flaky.tool_calling_ok is False  # any miss: fast path only (S7 not passed)
    assert OpenAICompatLLM("http://127.0.0.1:8080/v1", tools=TOOLS).tool_calling_ok is False  # unprobed = off
