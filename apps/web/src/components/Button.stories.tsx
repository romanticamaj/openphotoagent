import type { Meta, StoryObj } from "@storybook/react-vite";
import { Button } from "./Button";

// PF-080: every gated state keeps the button focusable and points aria-describedby at its reason
const meta: Meta<typeof Button> = { title: "Components/GatedButton", component: Button };
export default meta;
type S = StoryObj<typeof Button>;

const check: S["play"] = async ({ canvasElement }) => {
  const btn = canvasElement.querySelector("button")!;
  const why = document.getElementById(btn.getAttribute("aria-describedby") ?? "");
  if (!why || !why.textContent?.trim()) throw new Error("gated button without a linked reason");
  btn.focus();
  if (document.activeElement !== btn) throw new Error("gated button is not focusable");
};

export const Enabled: S = { args: { children: "建立整理計畫" } };
export const Downloading: S = {
  args: { children: "建立整理計畫", disabledReason: { code: "MODEL_DOWNLOADING", message: "Qwen3-VL 8B 下載中（42%），完成後自動解鎖" } },
  play: check,
};
export const NoTarget: S = {
  args: { children: "預覽", disabledReason: { code: "FS_NOT_WRITABLE", message: "沒有可寫入的輸出位置" } },
  play: check,
};
export const NeedsConfirmation: S = {
  args: { children: "移動", disabledReason: { code: "CONFIRM_REQUIRED", message: "請先勾選確認" } },
  play: check,
};
export const Busy: S = { args: { children: "核准並開始", disabledReason: { code: "BUSY", message: "處理中…" } }, play: check };
