import { afterEach, describe, expect, it, vi } from "vitest";
import { encodeTurned } from "./sharePhoto";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("making a shared photo", () => {
  it("[SHARE-039] 回歸：轉成 JPEG 用 toDataURL，不用 toBlob（驗證第 141 輪：WebView2 的 toBlob 每張約 1 秒，空白畫布也一樣；toDataURL 21 ms）", async () => {
    vi.stubGlobal("createImageBitmap", async () => ({ width: 4, height: 2, close: () => undefined }));
    const ctx = { translate: vi.fn(), rotate: vi.fn(), drawImage: vi.fn() };
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(ctx as never);
    const toBlob = vi.spyOn(HTMLCanvasElement.prototype, "toBlob").mockImplementation(() => undefined);
    vi.spyOn(HTMLCanvasElement.prototype, "toDataURL").mockReturnValue("data:image/jpeg;base64,AAEC");
    const out = await encodeTurned(new Blob(["x"]), "image/jpeg", 90);
    expect(toBlob).not.toHaveBeenCalled();
    expect(out.type).toBe("image/jpeg");
    expect([...new Uint8Array(await out.arrayBuffer())]).toEqual([0, 1, 2]);
    expect(ctx.rotate).toHaveBeenCalledWith(Math.PI / 2); // still turned
  });
});
