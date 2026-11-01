import type { Meta, StoryObj } from "@storybook/react-vite";
import { OperationProgress } from "./OperationProgress";
import type { Op } from "./model";

// ACT-006: four variants × every ETA state; the full variant's stepper shows skipped phases dashed
const NOW = Date.UTC(2026, 10, 1, 12, 0, 0);
const op = (eta: string | null, status = "running"): Op => ({
  id: `op-${eta}`,
  type: "job.label",
  status,
  fraction: 0.42,
  active_ms: 754_000,
  updated_at_ms: NOW,
  server_now_ms: NOW,
  eta: eta ? { state: eta, p50_s: 600 } : null,
  phases: [
    { name: "prepare", status: "done" },
    { name: "model_load", status: "skipped" },
    { name: "infer", status: "running" },
    { name: "postprocess", status: "pending" },
  ],
});

const meta: Meta<typeof OperationProgress> = { title: "Activity/OperationProgress", component: OperationProgress };
export default meta;
type S = StoryObj<typeof OperationProgress>;

const grid = (variant: "inline" | "compact" | "card" | "full") => ({
  render: () => (
    <div style={{ display: "grid", gap: 12, width: 420 }}>
      {["estimating", "rough", "precise", "stalled"].map((e) => (
        <OperationProgress key={e} variant={variant} op={op(e)} />
      ))}
      <OperationProgress variant={variant} op={op(null, "paused")} />
    </div>
  ),
});
export const Inline: S = grid("inline");
export const Compact: S = grid("compact");
export const Card: S = grid("card");
export const Full: S = grid("full");
