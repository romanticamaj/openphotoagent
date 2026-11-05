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
