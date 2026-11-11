import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "../../lib/i18n";
import { ActivityCenter } from "./ActivityCenter";
import { groupOps, indicatorState } from "./model";
import { OperationProgress } from "./OperationProgress";

const op = (o: Record<string, unknown>) => ({ id: "x", type: "model.download", status: "running", seq: 1, lane: "download", worker: "w1", fraction: 0.4, ...o });

describe("activity center", () => {
  it("[ETA-025] a finished operation has no time left to estimate", () => {
    const { container } = render(<OperationProgress op={op({ status: "succeeded", eta: { state: "estimating" } })} />);
    expect(container.querySelector("[data-eta]")).toBeNull();
  });

  it("[ACT-001] five indicator states", () => {
    expect(indicatorState([])).toEqual({ state: "idle", icon: "check", token: "--text-muted", labelKey: "activity.indicator.idle" });
    expect(indicatorState([op({})]).state).toBe("running");
    expect(indicatorState([op({ status: "paused" })]).state).toBe("paused");
    expect(indicatorState([op({ status: "failed", unread: true })]).state).toBe("failed");
    expect(indicatorState([op({ status: "interrupted" })]).state).toBe("interrupted");
    // precedence: interrupted > failed(unread) > paused > running
    expect(indicatorState([op({}), op({ status: "failed", unread: true }), op({ status: "interrupted" })]).state).toBe("interrupted");
    expect(indicatorState([op({ status: "failed", unread: true })]).token).toBe("--danger");
  });

  it("[ACT-002] grouped by worker × lane", () => {
    const g = groupOps([op({ id: "a", lane: "gpu" }), op({ id: "b", lane: "download" }), op({ id: "c", lane: "control", worker: null })]);
    expect(g.map((x) => x.key)).toEqual(["w1 · gpu", "w1 · download", "control"]);
  });

  it("[ACT-003] a failed row renders the problem's fixes, not just retry", () => {
    render(
      <ActivityCenter
        open
        ops={[op({ id: "f", status: "failed", error: { code: "HF_AUTH_REQUIRED", fixes: [{ id: "hf_token", type: "open_settings", primary: true }] } })]}
        onClose={() => {}}
      />,
    );
    expect(screen.getByRole("button", { name: /token/i })).toBeTruthy();
  });

  describe("elapsed", () => {
    beforeEach(() => vi.useFakeTimers());
    afterEach(() => vi.useRealTimers());

    it("[ACT-007] elapsed advances locally every second and stops when paused", () => {
      const now = Date.now();
      const { rerender } = render(<OperationProgress variant="compact" op={op({ active_ms: 10_000, updated_at_ms: now, server_now_ms: now })} />);
      const read = () => screen.getByTestId("elapsed").textContent;
      const t0 = read();
      act(() => vi.advanceTimersByTime(5000)); // SSE silent for 5 s
      const t5 = read();
      expect(t0).not.toBe(t5);
      rerender(<OperationProgress variant="compact" op={op({ status: "paused", active_ms: 15_000, updated_at_ms: now + 5000, server_now_ms: now + 5000 })} />);
      const p0 = read();
      act(() => vi.advanceTimersByTime(3000));
      expect(read()).toBe(p0);
    });
  });

  it("[ACT-005] Ctrl+J toggles the drawer", () => {
    let open = false;
    const toggle = () => (open = !open);
    render(<ActivityCenter open={false} ops={[]} onClose={() => {}} onToggle={toggle} />);
    fireEvent.keyDown(window, { key: "j", ctrlKey: true });
    expect(open).toBe(true);
  });
});

describe("what each activity is called (audit round 57)", () => {
  it("[ACT-015] 回歸：預覽寫成「預覽整理（未執行）」，不是「整理 完成」；內部名稱都有中文名（output.plan、dedup…）", async () => {
    const { opTypeKey } = await import("./model");
    const zh = (await import("@opa/i18n/locales/zh-TW.json")).default as { activity: { type: Record<string, string> } };
    expect(opTypeKey({ type: "job.label", subject: { job_kind: "preview" } })).toBe("job.preview");
    expect(zh.activity.type["job.preview"]).toBe("預覽整理（未執行）");
    for (const k of ["output.plan", "dedup", "face.reembed", "preview.stage", "model.load"]) expect(zh.activity.type[k], k).toBeTruthy();
  });
});

describe("an organize job named by where its plan stands (audit round 58)", () => {
  it("[ACT-017] 回歸：待核准的整理寫「整理計畫待核准」，捨棄的寫「已捨棄整理計畫」，不是「整理 完成」", async () => {
    const { opTypeKey } = await import("./model");
    const zh = (await import("@opa/i18n/locales/zh-TW.json")).default as { activity: { type: Record<string, string> } };
    const waiting = opTypeKey({ type: "job.label", subject: { job_kind: "organize", job_status: "awaiting_approval" } });
    const gone = opTypeKey({ type: "job.label", subject: { job_kind: "organize", job_status: "completed", outcome: "discarded" } });
    expect(zh.activity.type[waiting]).toBe("整理計畫待核准");
    expect(zh.activity.type[gone]).toBe("已捨棄整理計畫（未變更檔案）");
    expect(opTypeKey({ type: "job.label", subject: { job_kind: "organize", job_status: "completed" } })).toBe("job.label"); // applied: 整理
  });
});
