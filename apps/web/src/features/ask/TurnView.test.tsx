import { render, screen } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../../lib/i18n";

beforeAll(async () => {
  await i18n.changeLanguage("zh-TW");
});
vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, search }: { children: React.ReactNode; search?: { q?: string } }) => <a data-q={search?.q}>{children}</a>,
}));
vi.mock("../../lib/api", async (orig) => ({ ...(await orig<typeof import("../../lib/api")>()), mediaUrl: (id: string) => `/m/${id}` }));

import type { Turn } from "./stream";
import { TurnView } from "./AskPanel";

const turn = (over: Partial<Turn>): Turn => ({
  question: "我有幾張在海邊拍的照片？",
  turnId: "t1",
  mode: "local",
  intent: "count",
  path: null,
  steps: [],
  filters: { applied: {}, text: "海邊", explain: [] },
  photos: { total: 14, items: Array.from({ length: 14 }, (_, i) => `a${i}`), role: "candidates" },
  text: "共有 14 張。",
  cards: [],
  notices: [],
  status: "completed",
  ...over,
});

describe("an answer shows its photos", () => {
  it("[ASK-UI-010] 回歸：回答旁邊看得到找到的照片，可以到搜尋頁看全部（驗證第 1 輪：只有「共有 2 張」）", () => {
    render(<TurnView turn={turn({})} onRerun={() => {}} />);
    const strip = screen.getByTestId("ask-photos");
    expect(strip.querySelectorAll("img")).toHaveLength(8);
    const all = screen.getByText(/看全部 14 張/);
    expect(all.closest("a")?.getAttribute("data-q")).toBe("海邊");
  });
});

describe("ask chips", () => {
  it("[ASK-UI-010] 標籤 chip 顯示名稱，不是 scene.beach", () => {
    render(<TurnView turn={turn({ filters: { applied: { tags_any: ["scene.beach"] }, text: "海邊", explain: [], names: { "scene.beach": "海灘" } } })} onRerun={() => {}} />);
    expect(screen.queryByText(/scene\.beach/)).toBeNull();
    expect(screen.getByText(/標籤 海灘/)).toBeTruthy();
  });

  it("[ASK-UI-014] 回歸：人物 chip 顯示名字，不是 persons_all（驗證第 15 輪）", () => {
    render(<TurnView turn={turn({ filters: { applied: { persons_all: ["p9"] }, text: "", explain: [], names: { p9: "測試B" } } })} onRerun={() => {}} />);
    expect(screen.queryByText(/persons_all/)).toBeNull();
    expect(screen.getByText(/人物：測試B/)).toBeTruthy();
  });
});
