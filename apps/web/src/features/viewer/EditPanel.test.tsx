import { fireEvent, render, screen } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../../lib/i18n";
import { aspectCrop, EditPanel } from "./EditPanel";

beforeAll(async () => {
  await i18n.changeLanguage("zh-TW");
});

describe("the editor (ADR-0057)", () => {
  it("[EDIT-010] 滑桿有名稱與數值說明；改動即時預覽；完成只存非預設值；還原原圖、取消、Esc", () => {
    const change = vi.fn();
    const save = vi.fn();
    const revert = vi.fn();
    const cancel = vi.fn();
    render(<EditPanel initial={null} w={4000} h={3000} onChange={change} onSave={save} onRevert={revert} onCancel={cancel} />);
    const exp = screen.getByRole("slider", { name: "曝光" });
    expect(exp.getAttribute("aria-valuetext")).toBe("0 EV");
    fireEvent.change(exp, { target: { value: "0.3" } });
    expect(change).toHaveBeenLastCalledWith({ v: 1, exposure: 0.3 });
    expect(screen.getByRole("slider", { name: "曝光" }).getAttribute("aria-valuetext")).toBe("+0.3 EV");
    fireEvent.change(screen.getByRole("slider", { name: "飽和度" }), { target: { value: "-40" } });
    fireEvent.click(screen.getByRole("button", { name: "黑白" }));
    fireEvent.click(screen.getByRole("button", { name: "完成" }));
    expect(save).toHaveBeenCalledWith({ v: 1, exposure: 0.3, saturation: -40, filter: { name: "mono", strength: 100 } });
    fireEvent.click(screen.getByRole("button", { name: "還原原圖" }));
    expect(revert).toHaveBeenCalled();
    fireEvent.keyDown(screen.getByRole("slider", { name: "曝光" }), { key: "Escape" });
    expect(cancel).toHaveBeenCalled();
  });

  it("[EDIT-011] 裁切比例置中取最大的範圍；原始比例就是不裁", () => {
    expect(aspectCrop(1, 4000, 3000)).toEqual({ x: 0.125, y: 0, w: 0.75, h: 1 });
    expect(aspectCrop(16 / 9, 4000, 3000)).toEqual({ x: 0, y: 0.125, w: 1, h: 0.75 }); // 4000×2250
    expect(aspectCrop(null, 4000, 3000)).toBeUndefined();
    const save = vi.fn();
    render(<EditPanel initial={null} w={4000} h={3000} onChange={() => {}} onSave={save} onRevert={() => {}} onCancel={() => {}} />);
    fireEvent.click(screen.getByRole("radio", { name: "1:1" }));
    fireEvent.click(screen.getByRole("button", { name: "完成" }));
    expect(save).toHaveBeenCalledWith({ v: 1, crop: { x: 0.125, y: 0, w: 0.75, h: 1 } });
  });

  it("[EDIT-017] 「自動」填入光線的建議值，保留裁切與濾鏡，之後仍可再調", async () => {
    const change = vi.fn();
    const auto = vi.fn(async () => ({ v: 1 as const, exposure: 0.6, contrast: 20 }));
    render(<EditPanel initial={{ v: 1, crop: { x: 0, y: 0, w: 0.5, h: 1 }, filter: { name: "warm", strength: 100 } }} w={4000} h={3000} onChange={change} onSave={() => {}} onRevert={() => {}} onCancel={() => {}} auto={auto} />);
    fireEvent.click(screen.getByRole("button", { name: "自動" }));
    await vi.waitFor(() => expect(change).toHaveBeenLastCalledWith({ v: 1, exposure: 0.6, contrast: 20, crop: { x: 0, y: 0, w: 0.5, h: 1 }, filter: { name: "warm", strength: 100 } }));
    expect(screen.getByRole("slider", { name: "曝光" }).getAttribute("aria-valuetext")).toBe("+0.6 EV");
  });
});
