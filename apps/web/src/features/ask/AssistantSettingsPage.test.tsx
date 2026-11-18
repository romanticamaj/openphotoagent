import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../../lib/i18n";

beforeAll(async () => {
  await i18n.changeLanguage("zh-TW");
});
const s = vi.hoisted(() => ({ put: vi.fn() }));
vi.mock("../../lib/api", async (orig) => ({
  ...(await orig<typeof import("../../lib/api")>()),
  api: {
    assistantSettings: async () => ({ provider: "openai_compat", endpoint_url: "https://x", retention_days: 90, cloud_people: false }),
    putAssistantSettings: s.put,
  },
}));

import { AssistantSettingsPage } from "./AssistantSettingsPage";

describe("assistant settings", () => {
  it("[ASK-UI-013] 雲端模式另有「也可以依人物回答」的開關，預設關閉（ADR-0039）", async () => {
    s.put.mockResolvedValue({});
    render(
      <QueryClientProvider client={new QueryClient()}>
        <AssistantSettingsPage />
      </QueryClientProvider>,
    );
    const box = (await screen.findByLabelText(/依人物回答/)) as HTMLInputElement;
    expect(box.checked).toBe(false);
    fireEvent.click(box);
    fireEvent.click(screen.getByRole("button", { name: "儲存" }));
    await waitFor(() => expect(s.put).toHaveBeenCalledWith(expect.objectContaining({ cloud_people: true })));
  });
});
