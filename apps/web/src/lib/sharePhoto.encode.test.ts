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

describe("sharing an edited photo (ADR-0057)", () => {
  it("[EDIT-013] 分享的照片套用編輯：裁切後的大小、光線與色彩；裁掉的部分不會分享出去", async () => {
    vi.stubGlobal("createImageBitmap", async () => ({ width: 400, height: 300, close: () => undefined }));
    const px = new Uint8ClampedArray([100, 100, 100, 255]);
    const ctx = {
      translate: vi.fn(),
      rotate: vi.fn(),
      scale: vi.fn(),
      drawImage: vi.fn(),
      getImageData: vi.fn(() => ({ data: px })),
      putImageData: vi.fn(),
    };
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(ctx as never);
    let size: [number, number] = [0, 0];
    vi.spyOn(HTMLCanvasElement.prototype, "toDataURL").mockImplementation(function (this: HTMLCanvasElement) {
      size = [this.width, this.height];
      return "data:image/jpeg;base64,AAEC";
    });
    await encodeTurned(new Blob(["x"]), "image/jpeg", 0, { v: 1, exposure: 1, crop: { x: 0.25, y: 0, w: 0.5, h: 1 } });
    expect(size).toEqual([200, 300]); // only the crop
    expect(ctx.getImageData).toHaveBeenCalled();
    expect(px[0]).toBe(200); // +1 EV doubled the middle grey
    expect(ctx.putImageData).toHaveBeenCalled();
  });
});
