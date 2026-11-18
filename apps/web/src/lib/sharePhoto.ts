import { previewBlob } from "./api";
import { applyEdit, coverScale, isIdentity, type Edit } from "./editMath";
import { platform } from "./platform";

/**
 * Sharing one photo (ADR-0050). What leaves the app is the derived 2048 px preview the API already serves, re-encoded
 * in the page: the original is never opened (invariant 1), nothing is written in a library folder (invariant 3), and
 * re-encoding drops its metadata — where it was taken stays home unless the person sends the original themselves.
 */
export type SharedPhoto = { id: string; rot: number; takenAt: string | null; /** as edited in the app (EDIT-015) */ edit?: Edit | null };
export type ShareDeps = {
  preview: (id: string) => Promise<Blob>;
  /** draw the preview turned by `rot` degrees and encode it as `type` */
  encode: (b: Blob, type: string, rot: number, edit?: Edit | null) => Promise<Blob>;
  save: (b: Blob, name: string) => void;
  share: ((d: { files: File[] }) => Promise<void>) | undefined;
  canShare: ((d: { files: File[] }) => boolean) | undefined;
  writeClipboard: ((png: Blob) => Promise<void>) | undefined;
  /** desktop: the photo into a new folder inside one picked in the OS dialog; null when cancelled (SHARE-043) */
  saveToFolder?: ((name: string, bytes: Uint8Array) => Promise<{ dir: string; handle: string } | null>) | undefined;
};

/** photo-2025-11-11-0c1e2a11.jpg: the day it was taken and the last 8 of its id, so two photos of one day are two
 *  files (audit round 111, SHARE-009); plain characters only, whatever the id holds */
export function photoFileName(p: { id: string; takenAt: string | null }, ext: string): string {
  const day = p.takenAt && /^\d{4}-\d{2}-\d{2}/.test(p.takenAt) ? p.takenAt.slice(0, 10) : null;
  // the id's last 8: a UUIDv7 starts with when it was imported, the same for a whole import (audit round 112, SHARE-011)
  const short = p.id.replace(/[^0-9A-Za-z]/g, "").slice(-8);
  return `photo-${day ? `${day}-` : ""}${short}.${ext}`;
}

/** The system share sheet when it takes files; otherwise the photo is saved as a file. A cancelled sheet is no error. */
export async function sharePhoto(
  p: SharedPhoto,
  d: ShareDeps = browserDeps(),
): Promise<"shared" | "saved" | "cancelled" | { kind: "folder"; dir: string; handle: string }> {
  const jpeg = await d.encode(await d.preview(p.id), "image/jpeg", p.rot, p.edit ?? null);
  const name = photoFileName(p, "jpg");
  // the desktop: into a folder, said where; a download there was cancelled and still said 「已存」 (round 146, SHARE-043)
  if (d.saveToFolder) {
    const r = await d.saveToFolder(name, new Uint8Array(await jpeg.arrayBuffer()));
    return r ? { kind: "folder", ...r } : "cancelled";
  }
  const file = new File([jpeg], name, { type: "image/jpeg" });
  if (d.share && d.canShare?.({ files: [file] })) {
    try {
      await d.share({ files: [file] });
      return "shared";
    } catch (e) {
      if (e instanceof Error && e.name === "AbortError") return "cancelled";
      throw e;
    }
  }
  d.save(jpeg, name);
  return "saved";
}

/** The photo on the clipboard, as a PNG (the one image type clipboards take), to paste into a chat. */
export async function copyPhoto(p: SharedPhoto, d: ShareDeps = browserDeps()): Promise<"copied"> {
  if (!d.writeClipboard) throw new Error("CLIPBOARD_UNSUPPORTED");
  await d.writeClipboard(await d.encode(await d.preview(p.id), "image/png", p.rot, p.edit ?? null));
  return "copied";
}

export async function encodeTurned(b: Blob, type: string, rot: number, edit?: Edit | null): Promise<Blob> {
  const img = await createImageBitmap(b);
  const quarter = rot === 90 || rot === 270;
  const W = quarter ? img.height : img.width; // the photo after its quarter turn
  const H = quarter ? img.width : img.height;
  // an edit as the app shows it (ADR-0057, EDIT-013): only the crop leaves, straightened, with its light and colour
  const crop = edit?.crop ?? { x: 0, y: 0, w: 1, h: 1 };
  const cx = Math.round(crop.x * W);
  const cy = Math.round(crop.y * H);
  const c = document.createElement("canvas");
  c.width = Math.max(1, Math.round(crop.w * W));
  c.height = Math.max(1, Math.round(crop.h * H));
  const g = c.getContext("2d");
  if (!g) throw new Error("CANVAS_UNAVAILABLE");
  if (edit?.straighten) {
    const s = coverScale(edit.straighten, c.width, c.height);
    g.translate(c.width / 2, c.height / 2);
    g.rotate((edit.straighten * Math.PI) / 180);
    g.scale(s, s);
    g.translate(-c.width / 2, -c.height / 2);
  }
  g.translate(-cx, -cy);
  g.translate(W / 2, H / 2);
  g.rotate((rot * Math.PI) / 180);
  g.drawImage(img, -img.width / 2, -img.height / 2);
  img.close();
  if (edit && !isIdentity({ ...edit, crop: undefined, straighten: undefined })) {
    const data = g.getImageData(0, 0, c.width, c.height);
    applyEdit(data.data, edit);
    g.putImageData(data, 0, 0);
  }
  // toDataURL, not toBlob: in WebView2 toBlob took about 1 s a photo, even for a blank canvas (round 141, SHARE-039)
  const url = c.toDataURL(type, 0.92);
  const comma = url.indexOf(",");
  if (!url.startsWith(`data:${type}`) || comma < 0) throw new Error("ENCODE_FAILED");
  const bin = atob(url.slice(comma + 1));
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Blob([bytes], { type });
}

export function saveFile(b: Blob, name: string): void {
  const url = URL.createObjectURL(b);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

/** The browser's pieces. On the desktop the share sheet is left out: WebView2 says it can share files, but in the app's
 *  window the sheet never comes and the press does nothing (audit round 110, SHARE-008), so the photo is saved. */
export function browserDeps(kind: string = platform().kind, nav: Navigator | undefined = typeof navigator === "undefined" ? undefined : navigator): ShareDeps {
  const sheet = kind !== "desktop" && nav?.share ? nav : undefined;
  return {
    preview: previewBlob,
    encode: encodeTurned,
    save: saveFile,
    share: sheet ? (d) => sheet.share(d) : undefined,
    canShare: sheet?.canShare ? (d) => sheet.canShare(d) : undefined,
    writeClipboard:
      nav?.clipboard?.write && typeof ClipboardItem !== "undefined"
        ? (png) => nav.clipboard.write([new ClipboardItem({ "image/png": png })])
        : undefined,
    saveToFolder: desktopFolder(),
  };
}

function desktopFolder(): ShareDeps["saveToFolder"] {
  const p = platform();
  if (!p.pickShared || !p.writeShared) return undefined;
  const pick = p.pickShared;
  const write = p.writeShared;
  return async (name, bytes) => {
    const d = new Date();
    const day = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    const picked = await pick(day, 1);
    if (!picked) return null;
    await write(picked.handle, { name, bytes });
    return picked;
  };
}
