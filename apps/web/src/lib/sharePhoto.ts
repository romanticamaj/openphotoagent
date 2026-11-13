import { previewBlob } from "./api";

/**
 * Sharing one photo (ADR-0050). What leaves the app is the derived 2048 px preview the API already serves, re-encoded
 * in the page: the original is never opened (invariant 1), nothing is written in a library folder (invariant 3), and
 * re-encoding drops its metadata — where it was taken stays home unless the person sends the original themselves.
 */
export type SharedPhoto = { id: string; rot: number; takenAt: string | null };
export type ShareDeps = {
  preview: (id: string) => Promise<Blob>;
  /** draw the preview turned by `rot` degrees and encode it as `type` */
  encode: (b: Blob, type: string, rot: number) => Promise<Blob>;
  save: (b: Blob, name: string) => void;
  share: ((d: { files: File[] }) => Promise<void>) | undefined;
  canShare: ((d: { files: File[] }) => boolean) | undefined;
  writeClipboard: ((png: Blob) => Promise<void>) | undefined;
};

/** photo-2025-11-11.jpg, or photo-<first 8 of its id>.jpg: plain characters only, whatever the id holds */
export function photoFileName(p: { id: string; takenAt: string | null }, ext: string): string {
  const day = p.takenAt && /^\d{4}-\d{2}-\d{2}/.test(p.takenAt) ? p.takenAt.slice(0, 10) : null;
  return `photo-${day ?? p.id.replace(/[^0-9A-Za-z]/g, "").slice(0, 8)}.${ext}`;
}

/** The system share sheet when it takes files; otherwise the photo is saved as a file. A cancelled sheet is no error. */
export async function sharePhoto(p: SharedPhoto, d: ShareDeps = browserDeps()): Promise<"shared" | "saved" | "cancelled"> {
  const jpeg = await d.encode(await d.preview(p.id), "image/jpeg", p.rot);
  const name = photoFileName(p, "jpg");
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
  await d.writeClipboard(await d.encode(await d.preview(p.id), "image/png", p.rot));
  return "copied";
}

async function encodeTurned(b: Blob, type: string, rot: number): Promise<Blob> {
  const img = await createImageBitmap(b);
  const quarter = rot === 90 || rot === 270;
  const c = document.createElement("canvas");
  c.width = quarter ? img.height : img.width;
  c.height = quarter ? img.width : img.height;
  const g = c.getContext("2d");
  if (!g) throw new Error("CANVAS_UNAVAILABLE");
  g.translate(c.width / 2, c.height / 2);
  g.rotate((rot * Math.PI) / 180);
  g.drawImage(img, -img.width / 2, -img.height / 2);
  img.close();
  return new Promise((ok, no) => c.toBlob((out) => (out ? ok(out) : no(new Error("ENCODE_FAILED"))), type, 0.92));
}

function saveFile(b: Blob, name: string): void {
  const url = URL.createObjectURL(b);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

function browserDeps(): ShareDeps {
  const nav = typeof navigator === "undefined" ? undefined : navigator;
  return {
    preview: previewBlob,
    encode: encodeTurned,
    save: saveFile,
    share: nav?.share ? (d) => nav.share(d) : undefined,
    canShare: nav?.canShare ? (d) => nav.canShare(d) : undefined,
    writeClipboard:
      nav?.clipboard?.write && typeof ClipboardItem !== "undefined"
        ? (png) => nav.clipboard.write([new ClipboardItem({ "image/png": png })])
        : undefined,
  };
}
