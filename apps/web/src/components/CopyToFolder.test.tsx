import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../lib/i18n";

beforeAll(async () => {
  await i18n.changeLanguage("zh-TW");
});

vi.mock("@tanstack/react-router", async (orig) => ({
  ...(await orig<typeof import("@tanstack/react-router")>()),
  useNavigate: () => vi.fn(),
}));
vi.mock("../lib/api", async (orig) => {
  const real = await orig<typeof import("../lib/api")>();
  return { ...real, api: { roots: async () => [{ alias: "sorted", mode: "rw" }] } };
});

import { CopyToFolderButton } from "./CopyToFolder";

describe("複製到資料夾… (audit rounds 58–63)", () => {
  it("[COL-032] 回歸：在複製對話框按 Esc 只關對話框，不清掉選取；焦點回到按鈕（驗證第 130 輪：時間軸上 Esc 同時取消選取）", async () => {
    const outer = vi.fn();
    const onWindow = (e: KeyboardEvent) => e.key === "Escape" && outer(); // the timeline listens on the window
    window.addEventListener("keydown", onWindow);
    render(
      <QueryClientProvider client={new QueryClient()}>
        <CopyToFolderButton select={vi.fn()} />
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "複製到資料夾…" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(outer).not.toHaveBeenCalled();
    window.removeEventListener("keydown", onWindow);
  });
});
