import { thumbHashToDataURL } from "thumbhash";

const cache = new Map<string, string>();

/** ThumbHash placeholder painted before (and under) every thumbnail, so no tile is ever blank (10 §2.2, VIEW-003). */
export function placeholder(b64: string | null | undefined): string | undefined {
  if (!b64) return undefined;
  let url = cache.get(b64);
  if (url === undefined) {
    try {
      const bin = atob(b64);
      const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      url = thumbHashToDataURL(bytes);
    } catch {
      url = "";
    }
    if (cache.size > 5000) cache.clear();
    cache.set(b64, url);
  }
  return url || undefined;
}
