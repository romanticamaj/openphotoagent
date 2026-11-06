import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import i18n from "../lib/i18n";
import { ProblemCard } from "./ProblemCard";

describe("alternatives say what they cost (06 §4.3)", () => {
  it("[I18N-011] a choose_alternative fix shows its consequence, linked to the button, in both languages", async () => {
    for (const [lng, text] of [
      ["en", "Uses the Q4_K_M variant: fits in less VRAM, slightly lower accuracy."],
      ["zh-TW", "改用 Q4_K_M 版本：佔用較少 VRAM，準確度略低。"],
    ] as const) {
      await i18n.changeLanguage(lng);
      const { unmount } = render(
        <ProblemCard
          problem={{
            code: "VRAM_INSUFFICIENT",
            params: {},
            fixes: [
              { id: "q4_k_m", type: "choose_alternative", target: "q4_k_m", primary: true, consequence: { key: "fix_consequence.q4_k_m" } },
              { id: "cpu", type: "open_docs", target: "gpu" },
            ],
          }}
        />,
      );
      const note = screen.getByText(text);
      const btn = screen.getAllByRole("button")[0];
      expect(btn.getAttribute("aria-describedby")).toBe(note.id);
      expect(screen.getAllByRole("button")[1].getAttribute("aria-describedby")).toBeNull();
      unmount();
    }
  });
});
