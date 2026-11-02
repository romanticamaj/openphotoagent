// Platform seam (10 §B, §5.4): the desktop shell injects `window.__OPA_PLATFORM__` before the SPA loads; the web
// build has none of these and talks to the same-origin API.
export type Backend = { baseUrl: string; token: string };

export type Platform = {
  kind: "desktop" | "web";
  revealInFileManager?: (rootAlias: string, relPath: string) => Promise<void>;
  /** desktop: the sidecar's address and this launch's token (08 §1.3) — resolves once the API is up */
  backend?: () => Promise<Backend>;
  /** desktop: the worker's thumbnail cache directory (absolute path) */
  thumbsDir?: string;
  /** desktop: Tauri `convertFileSrc` — a URL the WebView can load through the asset protocol */
  assetUrl?: (absPath: string) => string;
  /** desktop: an OS notification; clicking it brings the window forward at `route` (08 §6, DESK-022) */
  notify?: (title: string, route: string) => void;
};

declare global {
  interface Window {
    __OPA_PLATFORM__?: Platform;
  }
}

export function platform(): Platform {
  return (typeof window !== "undefined" && window.__OPA_PLATFORM__) || { kind: "web" };
}

/** Where REST/SSE go and which headers they carry: same-origin with no token on the web. */
export async function endpoint(path: string): Promise<{ url: string; headers: Record<string, string> }> {
  const b = platform().backend;
  if (!b) return { url: path, headers: {} };
  const { baseUrl, token } = await b();
  return { url: `${baseUrl}${path}`, headers: { Authorization: `Bearer ${token}` } };
}
