import { afterEach, describe, expect, it, vi } from "vitest";
import { api, mediaUrl } from "./api";
import { readSse } from "./events";

afterEach(() => {
  delete window.__OPA_PLATFORM__;
  vi.unstubAllGlobals();
});

function desktop() {
  window.__OPA_PLATFORM__ = {
    kind: "desktop",
    backend: async () => ({ baseUrl: "http://127.0.0.1:47811", token: "tok" }),
    thumbsDir: "/data/worker/thumbs",
    assetUrl: (p: string) => `asset://localhost/${encodeURIComponent(p)}`,
  };
}

describe("desktop platform (10 §5.4, 08 §1.3)", () => {
  it("[DESK-036] REST goes to the sidecar's base URL with the launch token", async () => {
    desktop();
    const f = vi.fn(async () => new Response(JSON.stringify({ ok: 1 }), { status: 200 }));
    vi.stubGlobal("fetch", f);
    await api.roots();
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:47811/api/v1/roots");
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer tok");
  });

  it("[DESK-036] the web build keeps relative URLs and sends no token", async () => {
    const f = vi.fn(async () => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", f);
    await api.roots();
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/v1/roots");
    expect(new Headers(init.headers).get("Authorization")).toBeNull();
    expect(mediaUrl("a1", "t256", "abcdef")).toBe("/api/v1/assets/a1/media?variant=t256");
  });

  it("[DESK-036] thumbnails come from the local cache through the asset protocol", () => {
    desktop();
    expect(mediaUrl("a1", "t512", "abcdef")).toBe(`asset://localhost/${encodeURIComponent("/data/worker/thumbs/ab/abcdef.t512.webp")}`);
  });

  it("[DESK-036] SSE over fetch: events are parsed and the token is sent", async () => {
    desktop();
    const body = "event: model.installed\ndata: {\"key\":\"m\"}\n\n: keep-alive\n\nevent: job.updated\ndata: {\"id\":1}\n\n";
    const f = vi.fn(async () => new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } }));
    vi.stubGlobal("fetch", f);
    const got: Array<[string, unknown]> = [];
    await readSse("/api/v1/events", (kind, data) => got.push([kind, data]), new AbortController().signal);
    expect(got).toEqual([["model.installed", { key: "m" }], ["job.updated", { id: 1 }]]);
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:47811/api/v1/events");
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer tok");
  });
});
