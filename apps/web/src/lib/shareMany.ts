import { api, previewBlob } from "./api";
import { platform } from "./platform";
import { encodeTurned, photoFileName, saveFile } from "./sharePhoto";

/**
 * Sharing several photos at once (ADR-0054). Each is the 2048 px derived preview re-encoded in the page, as one photo
 * is (ADR-0050): no original is opened (invariant 1) and where it was taken stays home. The desktop shell writes them
 * into a new folder inside one the person picks in the OS dialog (never inside the library: invariant 3) and opens
 * it; the web build hands them to the share sheet, or saves one ZIP — many downloads in a row are blocked or asked
 * about by the browser.
 */
export type ManyDeps = {
  info: (id: string) => Promise<{ rot: number; takenAt: string | null }>;
  preview: (id: string) => Promise<Blob>;
  encode: (b: Blob, type: string, rot: number) => Promise<Blob>;
  /** desktop: the OS folder dialog, a new folder in it, the files written and the folder opened; null when cancelled */
  saveShared: ((day: string, files: Array<{ name: string; bytes: Uint8Array }>) => Promise<{ handle: string; dir: string; n: number } | null>) | undefined;
  share: ((d: { files: File[] }) => Promise<void>) | undefined;
  canShare: ((d: { files: File[] }) => boolean) | undefined;
  saveZip: (b: Blob, name: string) => void;
};
export type ManyResult =
  | { kind: "saved"; handle: string; dir: string; n: number; failed: number }
  | { kind: "shared"; n: number; failed: number }
  | { kind: "zipped"; name: string; n: number; failed: number }
  | { kind: "cancelled" };

/** At most this many in one share: each is made in the page. */
export const SHARE_MAX = 200;

export async function shareMany(ids: string[], d: ManyDeps = browserManyDeps(), day: string): Promise<ManyResult> {
  const made: Array<{ name: string; bytes: Uint8Array; blob: Blob }> = [];
  let failed = 0;
  let firstError: unknown = null;
  for (const id of ids) {
    try {
      const { rot, takenAt } = await d.info(id);
      const blob = await d.encode(await d.preview(id), "image/jpeg", rot);
      made.push({ name: photoFileName({ id, takenAt }, "jpg"), bytes: new Uint8Array(await blob.arrayBuffer()), blob });
    } catch (e) {
      failed += 1; // one photo whose preview is missing does not stop the rest; the result says so (SHARE-018)
      firstError ??= e;
    }
  }
  if (!made.length) throw firstError instanceof Error ? firstError : new Error("SHARE_FAILED");
  if (d.saveShared) {
    const r = await d.saveShared(day, made.map(({ name, bytes }) => ({ name, bytes })));
    return r ? { kind: "saved", handle: r.handle, dir: r.dir, n: r.n, failed } : { kind: "cancelled" };
  }
  const files = made.map((m) => new File([m.blob], m.name, { type: "image/jpeg" }));
  if (d.share && d.canShare?.({ files })) {
    try {
      await d.share({ files });
      return { kind: "shared", n: files.length, failed };
    } catch (e) {
      if (e instanceof Error && e.name === "AbortError") return { kind: "cancelled" };
      throw e;
    }
  }
  const name = `分享-${day}-${made.length}張.zip`;
  d.saveZip(new Blob([zipStore(made.map((m) => ({ name: m.name, data: m.bytes })))], { type: "application/zip" }), name);
  return { kind: "zipped", name, n: made.length, failed };
}

const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c >>> 0;
  }
  return t;
})();

export function crc32(b: Uint8Array): number {
  let c = 0xffffffff;
  for (const x of b) c = CRC_TABLE[(c ^ x) & 0xff]! ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

/** A ZIP that stores its files as they are (JPEGs do not shrink); names in UTF-8. */
export function zipStore(files: Array<{ name: string; data: Uint8Array }>): Uint8Array<ArrayBuffer> {
  const enc = new TextEncoder();
  const parts: Uint8Array[] = [];
  const central: Uint8Array[] = [];
  let offset = 0;
  for (const f of files) {
    const name = enc.encode(f.name);
    const crc = crc32(f.data);
    const local = new Uint8Array(30 + name.length);
    const lv = new DataView(local.buffer);
    lv.setUint32(0, 0x04034b50, true);
    lv.setUint16(4, 20, true);
    lv.setUint16(6, 0x0800, true); // UTF-8 names
    lv.setUint32(14, crc, true);
    lv.setUint32(18, f.data.length, true);
    lv.setUint32(22, f.data.length, true);
    lv.setUint16(26, name.length, true);
    local.set(name, 30);
    const cd = new Uint8Array(46 + name.length);
    const cv = new DataView(cd.buffer);
    cv.setUint32(0, 0x02014b50, true);
    cv.setUint16(4, 20, true);
    cv.setUint16(6, 20, true);
    cv.setUint16(8, 0x0800, true);
    cv.setUint32(16, crc, true);
    cv.setUint32(20, f.data.length, true);
    cv.setUint32(24, f.data.length, true);
    cv.setUint16(28, name.length, true);
    cv.setUint32(42, offset, true);
    cd.set(name, 46);
    parts.push(local, f.data);
    central.push(cd);
    offset += local.length + f.data.length;
  }
  const cdSize = central.reduce((n, c) => n + c.length, 0);
  const end = new Uint8Array(22);
  const ev = new DataView(end.buffer);
  ev.setUint32(0, 0x06054b50, true);
  ev.setUint16(8, files.length, true);
  ev.setUint16(10, files.length, true);
  ev.setUint32(12, cdSize, true);
  ev.setUint32(16, offset, true);
  const out = new Uint8Array(offset + cdSize + 22);
  let at = 0;
  for (const p of [...parts, ...central, end]) {
    out.set(p, at);
    at += p.length;
  }
  return out;
}

export function browserManyDeps(nav: Navigator | undefined = typeof navigator === "undefined" ? undefined : navigator): ManyDeps {
  const p = platform();
  const sheet = p.kind !== "desktop" && nav?.share ? nav : undefined;
  return {
    info: async (id) => {
      const a = await api.asset(id);
      return { rot: a.rotation ?? 0, takenAt: a.taken_at ?? null };
    },
    preview: previewBlob,
    encode: encodeTurned,
    saveShared: p.saveShared,
    share: sheet ? (d) => sheet.share(d) : undefined,
    canShare: sheet?.canShare ? (d) => sheet.canShare(d) : undefined,
    saveZip: saveFile,
  };
}
