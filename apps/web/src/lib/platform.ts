// Platform seam (10 §B): the desktop shell injects `window.__OPA_PLATFORM__`; the web build has none of these.
export type Platform = {
  kind: "desktop" | "web";
  revealInFileManager?: (rootAlias: string, relPath: string) => Promise<void>;
};

declare global {
  interface Window {
    __OPA_PLATFORM__?: Platform;
  }
}

export function platform(): Platform {
  return (typeof window !== "undefined" && window.__OPA_PLATFORM__) || { kind: "web" };
}
