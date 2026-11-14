// Platform seam (10 §B, §5.4): the desktop shell injects `window.__OPA_PLATFORM__` before the SPA loads; the web
// build has none of these and talks to the same-origin API.
export type Backend = { baseUrl: string; token: string };

/** What the shell says about start-up: the API cannot report that it failed to start (08 §1.4). */
export type BootStatus = { fatal: string | null; detail: string | null; reconnecting: string | null; logs: string };

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
  /** desktop: how the sidecars are doing */
  status?: () => Promise<BootStatus>;
  /** desktop: the OS folder dialog; the only way a root is added (invariant 2, C-09). Resolves to its alias, or null */
  addRoot?: (mode: "ro" | "rw") => Promise<string | null>;
  /** desktop: several photos into a new folder inside one picked in the OS dialog, then opened (ADR-0054); null when
   *  cancelled; rejects with SHARE_IN_ROOT for a folder inside the library */
  saveShared?: (day: string, files: Array<{ name: string; bytes: Uint8Array }>) => Promise<{ handle: string; dir: string; n: number } | null>;
  /** desktop: open a share folder again */
  openShared?: (handle: string) => Promise<string>;
};

declare global {
  interface Window {
    __OPA_PLATFORM__?: Platform;
  }
}

export function platform(): Platform {
  return (typeof window !== "undefined" && window.__OPA_PLATFORM__) || { kind: "web" };
}

/** Control plane session (v0.5): the CSRF token from login and the workspace the person is working in. */
const session: { csrf: string | null; workspace: string | null } = { csrf: null, workspace: null };
const WS_KEY = "opa.workspace";

export function setCsrf(token: string | null) {
  session.csrf = token;
}

export function setWorkspace(id: string | null) {
  session.workspace = id;
  try {
    if (id) window.localStorage.setItem(WS_KEY, id);
    else window.localStorage.removeItem(WS_KEY);
  } catch {
    /* private mode: the choice just isn't remembered */
  }
}

export function currentWorkspace(): string | null {
  if (session.workspace === null) {
    try {
      session.workspace = window.localStorage.getItem(WS_KEY);
    } catch {
      session.workspace = null;
    }
  }
  return session.workspace;
}

function sessionHeaders(): Record<string, string> {
  const h: Record<string, string> = {};
  if (session.csrf) h["X-CSRF-Token"] = session.csrf;
  const ws = currentWorkspace();
  if (ws) h["X-OPA-Workspace"] = ws;
  return h;
}

/** Where REST/SSE go and which headers they carry: same-origin with no token on the web. */
export async function endpoint(path: string): Promise<{ url: string; headers: Record<string, string> }> {
  const b = platform().backend;
  if (!b) return { url: path, headers: sessionHeaders() };
  const { baseUrl, token } = await b();
  return { url: `${baseUrl}${path}`, headers: { Authorization: `Bearer ${token}` } };
}
