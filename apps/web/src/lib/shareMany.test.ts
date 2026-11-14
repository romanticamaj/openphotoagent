import { describe, expect, it, vi } from "vitest";
import { crc32, shareMany, zipStore, type ManyDeps } from "./shareMany";

const jpeg = (s: string) => new Blob([s], { type: "image/jpeg" });

function deps(over: Partial<ManyDeps> = {}): ManyDeps & { encoded: Array<[string, number]> } {
  const encoded: Array<[string, number]> = [];
  return {
    encoded,
    info: vi.fn(async (id: string) => ({ rot: id === "b2" ? 90 : 0, takenAt: id === "a1" ? "2025-11-11T08:00:00" : null })),
    preview: vi.fn(async (id: string) => new Blob([id], { type: "image/webp" })),
    encode: vi.fn(async (b: Blob, _type: string, rot: number) => {
      const id = await b.text();
      encoded.push([id, rot]);
      return jpeg(`jpeg:${id}`);
    }),
    saveShared: undefined,
    share: undefined,
    canShare: undefined,
    saveZip: vi.fn(),
    ...over,
  };
}

/** The entries of a store-only ZIP, read back by its central directory. */
function unzip(z: Uint8Array): Array<{ name: string; data: Uint8Array; crc: number }> {
  const v = new DataView(z.buffer, z.byteOffset, z.byteLength);
  const end = z.byteLength - 22;
  expect(v.getUint32(end, true)).toBe(0x06054b50);
  const n = v.getUint16(end + 10, true);
  let at = v.getUint32(end + 16, true);
  const out = [];
  for (let i = 0; i < n; i++) {
    expect(v.getUint32(at, true)).toBe(0x02014b50);
    const crc = v.getUint32(at + 16, true);
    const size = v.getUint32(at + 24, true);
    const nameLen = v.getUint16(at + 28, true);
    const extra = v.getUint16(at + 30, true);
    const comment = v.getUint16(at + 32, true);
    const local = v.getUint32(at + 42, true);
    const name = new TextDecoder().decode(z.subarray(at + 46, at + 46 + nameLen));
    expect(v.getUint32(local, true)).toBe(0x04034b50);
    const start = local + 30 + v.getUint16(local + 26, true) + v.getUint16(local + 28, true);
    out.push({ name, data: z.subarray(start, start + size), crc });
    at += 46 + nameLen + extra + comment;
  }
  return out;
}

describe("sharing several photos (ADR-0054; audit round 124: one at a time only)", () => {
  it("[SHARE-016] 每張由衍生預覽轉成並套用自己的旋轉；桌面版一次交給殼存進新資料夾，不逐張下載", async () => {
    const saveShared = vi.fn(async (_day: string, files: Array<{ name: string; bytes: Uint8Array }>) => ({ handle: "h", dir: "D:\\out\\分享", n: files.length }));
    const d = deps({ saveShared });
    const r = await shareMany(["a1", "b2"], d, "2026-11-14");
    expect(d.preview).toHaveBeenCalledTimes(2); // the derived preview, never the original (invariant 1)
    expect(d.encoded).toEqual([["a1", 0], ["b2", 90]]);
    expect(saveShared).toHaveBeenCalledTimes(1);
    const [day, files] = saveShared.mock.calls[0]!;
    expect(day).toBe("2026-11-14");
    expect(files.map((f) => f.name)).toEqual(["photo-2025-11-11-a1.jpg", "photo-b2.jpg"]);
    expect(new TextDecoder().decode(files[1]!.bytes)).toBe("jpeg:b2");
    expect(r).toEqual({ kind: "saved", handle: "h", dir: "D:\\out\\分享", n: 2, failed: 0 });
    expect(d.saveZip).not.toHaveBeenCalled();
  });

  it("[SHARE-017] 取消選資料夾不算失敗、什麼都不存；資料夾在相片庫裡時說出原因", async () => {
    expect(await shareMany(["a1"], deps({ saveShared: async () => null }), "2026-11-14")).toEqual({ kind: "cancelled" });
    const inRoot = deps({ saveShared: async () => Promise.reject(new Error("SHARE_IN_ROOT")) });
    await expect(shareMany(["a1"], inRoot, "2026-11-14")).rejects.toThrow("SHARE_IN_ROOT");
  });

  it("[SHARE-018] 一張的預覽拿不到時略過它、其餘照存，並說有幾張失敗；全部失敗就拋出", async () => {
    const saveShared = vi.fn(async (_d: string, files: Array<{ name: string; bytes: Uint8Array }>) => ({ handle: "h", dir: "D:\\x", n: files.length }));
    const preview = vi.fn(async (id: string) => (id === "bad" ? Promise.reject(new Error("THUMB_MISSING")) : new Blob([id])));
    const r = await shareMany(["a1", "bad", "b2"], deps({ saveShared, preview }), "2026-11-14");
    expect(r).toMatchObject({ kind: "saved", n: 2, failed: 1 });
    expect(saveShared.mock.calls[0]![1]).toHaveLength(2);
    await expect(shareMany(["bad"], deps({ saveShared, preview }), "2026-11-14")).rejects.toThrow("THUMB_MISSING");
  });

  it("[SHARE-019] 網頁版能分享檔案時用系統分享（取消不算失敗）；不能時存成一個 ZIP，不逐張下載", async () => {
    const share = vi.fn(async () => undefined);
    expect(await shareMany(["a1", "b2"], deps({ share, canShare: () => true }), "2026-11-14")).toEqual({ kind: "shared", n: 2, failed: 0 });
    expect((share.mock.calls[0] as unknown as [{ files: File[] }])[0].files.map((f) => f.name)).toEqual(["photo-2025-11-11-a1.jpg", "photo-b2.jpg"]);
    const abort = Object.assign(new Error("x"), { name: "AbortError" });
    expect(await shareMany(["a1"], deps({ share: async () => Promise.reject(abort), canShare: () => true }), "2026-11-14")).toEqual({ kind: "cancelled" });
    const d = deps();
    expect(await shareMany(["a1", "b2"], d, "2026-11-14")).toEqual({ kind: "zipped", name: "分享-2026-11-14-2張.zip", n: 2, failed: 0 });
    expect(d.saveZip).toHaveBeenCalledTimes(1);
    const [zip, name] = (d.saveZip as ReturnType<typeof vi.fn>).mock.calls[0] as [Blob, string];
    expect(name).toBe("分享-2026-11-14-2張.zip");
    const entries = unzip(new Uint8Array(await zip.arrayBuffer()));
    expect(entries.map((e) => [e.name, new TextDecoder().decode(e.data)])).toEqual([
      ["photo-2025-11-11-a1.jpg", "jpeg:a1"],
      ["photo-b2.jpg", "jpeg:b2"],
    ]);
  });

  it("[SHARE-020] ZIP 的 CRC 與標頭正確，可讀回原內容", () => {
    expect(crc32(new TextEncoder().encode("abc"))).toBe(0x352441c2);
    expect(crc32(new Uint8Array())).toBe(0);
    const z = zipStore([{ name: "photo-x.jpg", data: new TextEncoder().encode("hello") }]);
    const [e] = unzip(z);
    expect(e!.name).toBe("photo-x.jpg");
    expect(new TextDecoder().decode(e!.data)).toBe("hello");
    expect(e!.crc).toBe(crc32(new TextEncoder().encode("hello")));
  });
});
