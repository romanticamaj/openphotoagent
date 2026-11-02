import type { Meta, StoryObj } from "@storybook/react-vite";
import { ReadinessChecklist } from "./OrganizeWizard";

const meta: Meta<typeof ReadinessChecklist> = { title: "Organize/ReadinessChecklist", component: ReadinessChecklist };
export default meta;
type S = StoryObj<typeof ReadinessChecklist>;

const vlm = (level: string, code?: string) => ({
  key: "qwen3-vl-8b-instruct@q4_k_m",
  compat: { level, reasons: code ? [{ code, params: { model: "Qwen3-VL 8B", need: 8.5, total: 6, pct: 30 } }] : [] },
});
export const Ready: S = { args: { models: [vlm("ok")], blocked: false, autoStart: false, setAutoStart: () => {} } };
export const Downloading: S = { args: { models: [vlm("ok")], blocked: true, autoStart: true, setAutoStart: () => {} } };
export const Degraded: S = { args: { models: [vlm("degraded", "VRAM_SEQUENTIAL")], blocked: false, autoStart: false, setAutoStart: () => {} } };
