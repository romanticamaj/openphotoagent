import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../../lib/i18n";

beforeAll(async () => {
  await i18n.changeLanguage("zh-TW");
});

vi.mock("@tanstack/react-router", async (orig) => ({
  ...(await orig<typeof import("@tanstack/react-router")>()),
  Link: ({ to, children, ...rest }: { to: string; children: React.ReactNode }) => (
    <a href={to} {...rest}>
      {children}
    </a>
  ),
  useNavigate: () => vi.fn(),
}));
vi.mock("../../components/LibraryFolders", () => ({
  LibraryFolders: ({ onAct, action }: { onAct: (x: unknown) => void; action: () => string }) => (
    <button type="button" onClick={() => onAct({ id: "s1", root_alias: "photos", sub_path: "trip", photos: 3, analysed: 3 })}>
      {action()}
    </button>
  ),
}));
vi.mock("../../components/FolderPicker", () => ({
  FolderPicker: () => <button type="button">photos</button>,
}));
vi.mock("../../lib/events", () => ({ useServerEvents: () => undefined }));
vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  api: {
    roots: async () => [
      { alias: "photos", mode: "ro" },
      { alias: "sorted", mode: "rw" },
    ],
    preset: async () => ({ categories: [] }),
    jobs: async () => [],
    readiness: async () => ({ ready: true, steps: [] }),
  },
}));

import { OrganizeWizard } from "./OrganizeWizard";

const ui = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <OrganizeWizard />
    </QueryClientProvider>,
  );

describe("整理 looks like what it does (ADR-0044)", () => {
  it("[ORG-017] 回歸：整理的第一個畫面是三個步驟、輸出一直看得到、資料夾樹收在「圖庫以外的資料夾…」（驗證第 55～56 輪：與匯入一模一樣）", async () => {
    ui();
    expect(screen.getByRole("heading", { level: 1, name: "整理照片" })).toBeTruthy();
    const steps = screen.getByRole("list", { name: "整理步驟" }).querySelectorAll("li");
    expect([...steps].map((s) => s.textContent)).toEqual(["1選照片", "2整理方式", "3輸出與預覽"]);
    expect(steps[0].getAttribute("aria-current")).toBe("step");
    await waitFor(() => expect(screen.getByTestId("organize-strip").textContent).toContain("sorted"));
    expect(screen.getByTestId("organize-strip").textContent).toContain("複製，原檔不動");
    expect(screen.getByRole("link", { name: /從照片或相簿挑選/ }).getAttribute("href")).toBe("/search");
    const tree = screen.getByRole("button", { name: "photos" }).closest("details");
    expect(tree?.open).toBe(false);
    expect(tree?.querySelector("summary")?.textContent).toBe("圖庫以外的資料夾…");
    fireEvent.click(screen.getByRole("button", { name: "整理這個資料夾" }));
    await waitFor(() => expect(screen.getByTestId("organize-strip").textContent).toContain("photos/trip"));
    expect(screen.getByRole("list", { name: "整理步驟" }).querySelectorAll("li")[1].getAttribute("aria-current")).toBe("step");
  });
});
