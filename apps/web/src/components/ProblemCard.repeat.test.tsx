import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import i18n from "../lib/i18n";
import { ProblemCard } from "./ProblemCard";

describe("a problem is said once", () => {
  it("[DOC-023] 回歸：內文和標題相同時不重印；代價說明不重複按鈕名稱（驗證第 1 輪）", async () => {
    await i18n.changeLanguage("zh-TW");
    render(
      <ProblemCard
        problem={{
          code: "GPU_BUSY_EXTERNAL",
          params: { gb: 12.5, process: "chrome.exe, Spotify.exe" },
          fixes: [
            { id: "retry", type: "retry" },
            { id: "smaller_variant", type: "choose_alternative", consequence: { key: "fix_consequence.smaller_variant" } },
          ],
        }}
      />,
    );
    expect(screen.getAllByText(/其他程式正在佔用 12.5 GB VRAM/)).toHaveLength(1);
    const notes = screen.getAllByRole("listitem").map((li) => li.textContent ?? "");
    expect(notes.every((n) => (n.match(/改用較小的版本/g) ?? []).length <= 1)).toBe(true);
  });
});
