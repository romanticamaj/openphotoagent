import { describe, expect, it } from "vitest";
import { initialTurn, parseSse, reduce, renderAnswer, seconds } from "./stream";

const sse = (ev: string, data: unknown) => `event: ${ev}\ndata: ${JSON.stringify(data)}\n\n`;

describe("ask stream", () => {
  it("[ASK-SSE-001] parses SSE blocks across chunk boundaries and keeps the order", () => {
    const text = sse("turn.started", { turn_id: "t1", mode: "local", intent: "retrieval", path: "fast" }) + sse("token", { text: "找到" });
    const cut = 17;
    const a = parseSse("", text.slice(0, cut));
    expect(a.events).toEqual([]);
    const b = parseSse(a.rest, text.slice(cut));
    expect(b.events.map((e) => e.event)).toEqual(["turn.started", "token"]);
    expect(b.rest).toBe("");
  });

  it("[ASK-SSE-002] steps carry their estimate, then their elapsed time and summary", () => {
    let s = initialTurn("海邊的貓");
    s = reduce(s, { event: "step.started", data: { step_id: "s2", kind: "tool", label: "搜尋照片", tool: "search_photos", eta_ms: 300 } });
    expect(s.steps[0]).toMatchObject({ status: "running", eta_ms: 300 });
    s = reduce(s, { event: "step.completed", data: { step_id: "s2", status: "ok", elapsed_ms: 212, summary: "找到 5 張" } });
    expect(s.steps[0]).toMatchObject({ status: "ok", elapsed_ms: 212, summary: "找到 5 張" });
    expect(seconds(212)).toBe("0.2");
    expect(seconds(1530)).toBe("1.5");
  });

  it("[ASK-SSE-004] filters arrive as editable chips with their text", () => {
    let s = initialTurn("2023 年的照片");
    s = reduce(s, { event: "filters", data: { applied_filters: { taken_at: { from: "2023-01-01", to: "2024-01-01" } }, text: "照片", planner_explain: ["「2023 年」→ 日期"], editable: true } });
    expect(s.filters).toEqual({ applied: { taken_at: { from: "2023-01-01", to: "2024-01-01" } }, text: "照片", explain: ["「2023 年」→ 日期"] });
  });

  it("[ASK-GRD-006] the answer renders text and bold only: links and images never become markup", () => {
    const parts = renderAnswer("**重點** [點我](http://evil.example) ![](http://evil.example/p.png) [[photo:a1]]");
    expect(parts).toEqual([
      { kind: "bold", text: "重點" },
      { kind: "text", text: " 點我  " },
      { kind: "photo", id: "a1" },
    ]);
  });

  it("[ASK-ACT-002] the plan card comes from the event payload, not from the answer text", () => {
    let s = initialTurn("把這些貓複製到京都資料夾");
    s = reduce(s, { event: "card", data: { kind: "plan", payload: { plan_id: "p1", action: "copy", count: 3, target: "sorted/京都", needs_approval: true } } });
    s = reduce(s, { event: "token", data: { text: "已經移動 999 個檔案" } });
    expect(s.cards[0]).toEqual({ kind: "plan", payload: { plan_id: "p1", action: "copy", count: 3, target: "sorted/京都", needs_approval: true } });
  });

  it("[ASK-SSE-003] completion carries the final status", () => {
    let s = initialTurn("x");
    s = reduce(s, { event: "turn.completed", data: { status: "cancelled", grounding: { dropped_citations: 0, number_fixes: 0 } } });
    expect(s.status).toBe("cancelled");
  });
});
