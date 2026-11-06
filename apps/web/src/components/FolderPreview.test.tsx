import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../lib/i18n";

beforeAll(async () => {
  await i18n.changeLanguage("zh-TW");
});

const preview = {
  photos: 478,
  videos: 9,
  others: 5,
  bytes: 5_200_000_000,
  truncated: false,
  subfolders: [{ name: "day1", photos: 117, videos: 0 }],
  samples: ["trip/a.jpg", "trip/b.jpg"],
  taken_from: "2024-04-03T09:00:00+08:00",
  taken_to: "2024-04-09T18:00:00+08:00",
};
const state = vi.hoisted(() => ({ preview: null as unknown, thumbs: [] as string[], fail: false }));
vi.mock("../lib/api", async (orig) => ({
  ...(await orig<typeof import("../lib/api")>()),
  api: {
    roots: async () => [{ worker_id: "w", alias: "photos", mode: "ro" }],
    browse: async (_r: string, p: string) => ({ dirs: p === "" ? ["trip"] : [] }),
    folderPreview: async () => {
      if (state.fail) throw new Error("offline");
      return state.preview;
    },
    folderThumb: async (_r: string, p: string) => {
      state.thumbs.push(p);
      return { thumbnail_b64: "AAAA" };
    },
  },
}));

import { FolderPicker } from "./FolderPicker";

const ui = (onPick = vi.fn()) => {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <FolderPicker onPick={onPick} action="加入圖庫" />
    </QueryClientProvider>,
  );
  return onPick;
};

describe("seeing a folder before adding it (ADR-0029)", () => {
  it("[IMP-010] 選資料夾後看得到張數、大小、日期、略過的檔案與樣本縮圖", async () => {
    state.preview = preview;
    state.fail = false;
    state.thumbs = [];
    ui();
    fireEvent.click(await screen.findByRole("button", { name: "photos" }));
    const pane = await screen.findByTestId("folder-preview");
    await within(pane).findByText("478");
    expect(within(pane).getByText("9")).toBeTruthy();
    expect(pane.textContent).toContain("2024");
    expect(pane.textContent).toContain("5");
    await waitFor(() => expect(within(pane).getAllByRole("img").length).toBe(2));
    expect(state.thumbs).toEqual(["trip/a.jpg", "trip/b.jpg"]);
    expect(pane.textContent).toContain("不會修改");
  });

  it("[IMP-010] 子資料夾可以展開，選到的資料夾交給呼叫端", async () => {
    state.preview = preview;
    const pick = ui();
    fireEvent.click(await screen.findByRole("button", { name: "photos" }));
    fireEvent.click(await screen.findByRole("button", { name: "trip" }));
    await waitFor(() => expect(screen.getByTestId("current-path").textContent).toContain("trip"));
    fireEvent.click(await screen.findByRole("button", { name: "加入圖庫" }));
    expect(pick).toHaveBeenCalledWith("photos", "trip");
  });

  it("[IMP-010] 空資料夾不給加入，並說明原因", async () => {
    state.preview = { ...preview, photos: 0, videos: 0, others: 0, bytes: 0, samples: [], subfolders: [], taken_from: null, taken_to: null };
    const pick = ui();
    fireEvent.click(await screen.findByRole("button", { name: "photos" }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("沒有照片"));
    fireEvent.click(screen.getByTestId("use-folder"));
    expect(pick).not.toHaveBeenCalled();
  });

  it("[IMP-010] 讀不到預覽時說明，仍然可以加入", async () => {
    state.fail = true;
    const pick = ui();
    fireEvent.click(await screen.findByRole("button", { name: "photos" }));
    await screen.findByText(/暫時看不到/);
    fireEvent.click(screen.getByTestId("use-folder"));
    expect(pick).toHaveBeenCalledWith("photos", "");
  });
});
