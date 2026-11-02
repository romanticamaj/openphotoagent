import type { Meta, StoryObj } from "@storybook/react-vite";
import { CompatBadge } from "./CompatBadge";

const meta: Meta<typeof CompatBadge> = { title: "Components/CompatBadge", component: CompatBadge };
export default meta;
type S = StoryObj<typeof CompatBadge>;

export const Levels: S = {
  render: () => (
    <div style={{ display: "flex", gap: 16 }}>
      <CompatBadge compat={{ level: "ok", reasons: [] }} />
      <CompatBadge compat={{ level: "degraded", reasons: [{ code: "VRAM_SEQUENTIAL", params: { pct: 30 } }] }} />
      <CompatBadge compat={{ level: "blocked", reasons: [{ code: "VRAM_INSUFFICIENT", params: { model: "Qwen3-VL 8B", need: 8.5, total: 6 } }] }} />
      <CompatBadge compat={null} />
    </div>
  ),
};
