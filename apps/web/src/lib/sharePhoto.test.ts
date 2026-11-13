import { describe, expect, it, vi } from "vitest";
import { copyPhoto, photoFileName, sharePhoto, type ShareDeps } from "./sharePhoto";

const jpeg = new Blob(["j"], { type: "image/jpeg" });
const png = new Blob(["p"], { type: "image/png" });

function deps(over: Partial<ShareDeps> = {}): ShareDeps & { saved: Array<[Blob, string]>; encoded: Array<[string, number]> } {
  const saved: Array<[Blob, string]> = [];
  const encoded: Array<[string, number]> = [];
  return {
    saved,
    encoded,
    preview: vi.fn(async () => new Blob(["w"], { type: "image/webp" })),
    encode: vi.fn(async (_b: Blob, type: string, rot: number) => (encoded.push([type, rot]), type === "image/png" ? png : jpeg)),
    save: (b: Blob, name: string) => void saved.push([b, name]),
    share: undefined,
    canShare: undefined,
    writeClipboard: undefined,
    ...over,
  };
}

describe("sharing one photo (audit round 109: no share or export)", () => {
  it("[SHARE-001] 有系統分享時，分享一個 JPEG 檔（由衍生預覽轉成，套用旋轉，不讀原檔）", async () => {
    const share = vi.fn(async () => undefined);
    const d = deps({ share, canShare: () => true });
    expect(await sharePhoto({ id: "a1", rot: 90, takenAt: "2025-11-11T08:00:00" }, d)).toBe("shared");
    expect(d.preview).toHaveBeenCalledWith("a1"); // the derived preview, never the original (invariant 1)
    expect(d.encoded).toEqual([["image/jpeg", 90]]); // turned as it is shown
    const files = (share.mock.calls[0] as unknown as [{ files: File[] }])[0].files;
    expect(files).toHaveLength(1);
    expect(files[0]!.name).toBe("photo-2025-11-11.jpg");
    expect(files[0]!.type).toBe("image/jpeg");
    expect(d.saved).toEqual([]);
  });

  it("[SHARE-002] 沒有系統分享（或不能分享檔案）時，存成一個 JPEG 檔", async () => {
    for (const over of [{}, { share: vi.fn(), canShare: () => false }]) {
      const d = deps(over);
      expect(await sharePhoto({ id: "a1", rot: 0, takenAt: null }, d)).toBe("saved");
      expect(d.saved).toHaveLength(1);
      expect(d.saved[0]![1]).toBe("photo-a1.jpg");
    }
  });

  it("[SHARE-003] 使用者取消分享時不算失敗，也不改存檔；其他錯誤往外拋", async () => {
    const abort = Object.assign(new Error("cancel"), { name: "AbortError" });
    const d = deps({ share: vi.fn(async () => Promise.reject(abort)), canShare: () => true });
    expect(await sharePhoto({ id: "a1", rot: 0, takenAt: null }, d)).toBe("cancelled");
    expect(d.saved).toEqual([]);
    const e = deps({ preview: vi.fn(async () => Promise.reject(new Error("THUMB_MISSING"))) });
    await expect(sharePhoto({ id: "a1", rot: 0, takenAt: null }, e)).rejects.toThrow("THUMB_MISSING");
  });

  it("[SHARE-004] 複製圖片：剪貼簿寫入一張 PNG（套用旋轉）；沒有剪貼簿圖片功能時說不支援", async () => {
    const writeClipboard = vi.fn(async () => undefined);
    const d = deps({ writeClipboard });
    expect(await copyPhoto({ id: "a1", rot: 180, takenAt: null }, d)).toBe("copied");
    expect(d.encoded).toEqual([["image/png", 180]]);
    expect(writeClipboard).toHaveBeenCalledWith(png);
    await expect(copyPhoto({ id: "a1", rot: 0, takenAt: null }, deps())).rejects.toThrow("CLIPBOARD_UNSUPPORTED");
  });

  it("[SHARE-005] 檔名：有拍攝日期用日期，否則用照片編號前 8 碼，只用安全字元", () => {
    expect(photoFileName({ id: "0192ab34-cdef", takenAt: "2024-02-10T00:00:00Z" }, "jpg")).toBe("photo-2024-02-10.jpg");
    expect(photoFileName({ id: "0192ab34-cdef", takenAt: null }, "png")).toBe("photo-0192ab34.png");
    expect(photoFileName({ id: "../../x", takenAt: null }, "jpg")).toBe("photo-x.jpg");
  });
});
