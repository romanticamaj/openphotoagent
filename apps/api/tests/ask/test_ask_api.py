"""「AI 問照片」 over the API (10 §8–§10; 09b §5): SSE turns, tool steps with times, grounding, actions as plan cards,
privacy in cloud mode, retention. The model is FakeLLM; tools are the shared MCP handlers."""

import json
import threading
import time

import pytest
from opagent_assistant.llm import FakeLLM, Reply
from opagent_testing.mcp_harness import M


def events(resp_text: str) -> list[tuple[str, dict]]:
    out = []
    for block in resp_text.strip().split("\n\n"):
        ev = data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if ev:
            out.append((ev, data))
    return out


@pytest.fixture
def m(embedded_pg, tmp_path):
    x = M(embedded_pg.dsn, tmp_path)
    x.cats = [x.add(f"2025/cat{i}.jpg", caption="海邊的貓", tags=("animal.cat",)) for i in range(5)]
    x.add("2025/dog.jpg", caption="狗", tags=("animal.dog",))
    x.index()
    yield x
    x.eng.dispose()


def ask(m, message, llm, **body):
    m.client.app.state.opa.llm = llm
    r = m.client.post("/api/v1/ask", json={"message": message, **body}, headers=m.ui)
    assert r.status_code == 200, r.text
    return events(r.text)


def _cite_first(messages):
    seen = [x for msg in messages if msg["role"] == "tool" for x in json.loads(msg["content"]).get("ids", [])]
    return Reply(text=f"找到這些貓 [[photo:{seen[0]}]] [[photo:not-real]]")


@pytest.mark.integration
@pytest.mark.tc("ASK-SSE-001", "ASK-SSE-002", "ASK-GRD-001")
def test_sse_001_002_retrieval_turn(m):
    ev = ask(m, "海邊的貓", FakeLLM([_cite_first]))
    kinds = [e for e, _ in ev]
    assert kinds[0] == "turn.started" and kinds[-1] == "turn.completed"
    order = [k for k in kinds if k in ("step.started", "filters", "step.completed", "photos", "token", "citation")]
    assert order[:4] == ["step.started", "filters", "step.completed", "step.started"]
    assert "photos" in order and "token" in order and "citation" in order
    starts = [d for e, d in ev if e == "step.started"]
    assert starts[0]["kind"] == "understand" and starts[1]["tool"] == "search_photos"
    tool_steps = {d["step_id"] for d in starts if d.get("tool")}
    assert all("eta_ms" in d for d in starts if d.get("tool"))
    done = [d for e, d in ev if e == "step.completed" and d["step_id"] in tool_steps]
    assert done and all(isinstance(d["elapsed_ms"], int) for d in done)
    photos = next(d for e, d in ev if e == "photos")
    assert photos["role"] == "candidates" and photos["total"] == 5
    fin = ev[-1][1]
    assert fin["grounding"]["dropped_citations"] == 1
    text = "".join(d["text"] for e, d in ev if e == "token")
    assert "not-real" not in text and ev[0][1]["mode"] == "local" and ev[0][1]["intent"] == "retrieval"


@pytest.mark.integration
@pytest.mark.tc("ASK-GRD-002", "ASK-GRD-003")
def test_counts_come_from_count_photos(m):
    ev = ask(m, "去年拍了幾張貓？", FakeLLM([Reply(text="你拍了 1,300 張貓。")]), today="2026-11-02")
    tools = [d.get("tool") for e, d in ev if e == "step.started"]
    assert "count_photos" in tools and ev[0][1]["intent"] == "count"
    text = "".join(d["text"] for e, d in ev if e == "token")
    assert "1,300" not in text and "5" in text and ev[-1][1]["grounding"]["number_fixes"] == 1


@pytest.mark.integration
@pytest.mark.tc("ASK-GRD-004")
def test_nothing_found_is_a_refusal(m):
    ev = ask(m, "海邊的長頸鹿", FakeLLM([Reply(text="我找到 5 張長頸鹿！")]))
    text = "".join(d["text"] for e, d in ev if e == "token")
    assert text.startswith("找不到") and "找到 5 張" not in text


@pytest.mark.integration
@pytest.mark.tc("ASK-ACT-001")
def test_act_001_actions_only_produce_a_plan(m):
    llm = FakeLLM([Reply(text="好的，我會整理。")])
    ev = ask(m, "把這些貓複製到京都資料夾", llm, context={"photo_ids": m.cats[:3]})
    card = next(d for e, d in ev if e == "card")
    assert card["kind"] == "plan" and card["payload"]["count"] == 3 and card["payload"]["needs_approval"] is True
    offered = {t for msg in llm.seen for t in msg[0].get("tools", [])} if llm.seen else set()
    assert "apply_organize" not in offered and "undo_organize" not in offered
    assert m.sql("select count(*) from worker_task where kind='apply'").scalar() == 0
    assert m.sql("select status from output_plan").scalar() == "draft"


@pytest.mark.integration
@pytest.mark.tc("ASK-SEC-001")
def test_sec_001_shared_registry_permissions(m):
    llm = FakeLLM([Reply(tool="find_photos_of_person", args={"person_ids": ["p1"]}), Reply(text="好")])
    ev = ask(m, "比較一下 2023 和 2024 誰拍得多", llm)
    step = next(d for e, d in ev if e == "step.completed" and d.get("tool") == "find_photos_of_person")
    assert step["status"] == "error"
    row = m.sql("select actor_kind, status from mcp_call_log where tool='find_photos_of_person'").one()
    assert row.actor_kind == "builtin-assistant" and row.status == "denied"


@pytest.mark.integration
@pytest.mark.tc("ASK-LOOP-001", "ASK-LOOP-003")
def test_loop_limits_and_fast_path_without_tool_calling(m):
    ev = ask(m, "比較 2023 和 2024 每月拍了多少", FakeLLM(endless_tool="list_facets"))
    assert [d for e, d in ev if e == "step.started" and d.get("tool")].__len__() == 6
    assert any(e == "notice" and d["code"] == "step_limit" for e, d in ev)
    ev2 = ask(m, "比較 2023 和 2024 每月拍了多少", FakeLLM([Reply(text="")], tool_calling_ok=False))
    assert ev2[0][1]["path"] == "fast" and not [1 for e, _ in ev2 if e == "error"]


@pytest.mark.integration
@pytest.mark.tc("ASK-SSE-003")
def test_sse_003_cancel_within_a_second(m):
    st = m.client.app.state.opa
    st.llm = FakeLLM(endless_tool="list_tags")
    st.ask_tool_delay_s = 0.3
    out = {}

    def run():
        r = m.client.post("/api/v1/ask", json={"message": "比較每年拍了多少 2023 2024"}, headers=m.ui)
        out["ev"] = events(r.text)

    t = threading.Thread(target=run)
    t.start()
    while not st.ask_turns:
        time.sleep(0.01)
    turn = next(iter(st.ask_turns))
    t0 = time.monotonic()
    assert m.client.post(f"/api/v1/ask/turns/{turn}/cancel", headers=m.ui).status_code == 200
    t.join(5)
    assert time.monotonic() - t0 < 1.0
    assert out["ev"][-1][0] == "turn.completed" and out["ev"][-1][1]["status"] == "cancelled"
    assert m.sql("select status from message where turn_id=:t and role='assistant'", t=turn).scalar() == "cancelled"


@pytest.mark.integration
@pytest.mark.tc("ASK-PRV-001", "ASK-PRV-002", "ASK-PRV-003", "ASK-PRV-004")
def test_cloud_mode_privacy(m):
    r = m.client.patch("/api/v1/assistant-settings", json={"provider": "openai_compat",
                                                            "endpoint_url": "https://llm.example/v1"}, headers=m.ui)
    assert r.status_code == 200 and r.json()["media_policy"] == "none"
    llm = FakeLLM([Reply(text="好")])
    llm.cloud = True
    ev = ask(m, "媽媽在海邊的貓", llm, person_names={"媽媽": "p1"})
    assert ev[0][1]["mode"] == "cloud" and any(e == "notice" and d["code"] == "cloud_mode" for e, d in ev)
    sent = json.dumps(llm.seen, ensure_ascii=False)
    assert "媽媽" not in sent and "人物A" in sent and '"image"' not in sent and "base64" not in sent
    log = m.sql("select endpoint, bytes_out, images from assistant_egress_log").one()
    assert log.endpoint == "https://llm.example/v1" and log.bytes_out > 0 and log.images == 0


@pytest.mark.integration
@pytest.mark.tc("ASK-MEM-002")
def test_mem_002_retention_zero_and_delete(m):
    ask(m, "海邊的貓", FakeLLM([_cite_first]))
    conv = m.client.get("/api/v1/conversations", headers=m.ui).json()
    assert len(conv) == 1
    assert m.client.delete(f"/api/v1/conversations/{conv[0]['id']}", headers=m.ui).status_code == 200
    for t in ("conversation", "message", "message_step", "message_citation"):
        assert m.sql(f"select count(*) from {t}").scalar() == 0, t
    m.client.patch("/api/v1/assistant-settings", json={"retention_days": 0}, headers=m.ui)
    ask(m, "海邊的貓", FakeLLM([_cite_first]))
    assert m.sql("select count(*) from message").scalar() == 0
