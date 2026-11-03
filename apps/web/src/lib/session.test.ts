import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";
import { setCsrf, setWorkspace } from "./platform";

afterEach(() => {
  setCsrf(null);
  setWorkspace(null);
  vi.unstubAllGlobals();
});

describe("control-plane web client (02 §6.3, ADR-0016)", () => {
  it("[MU-AUTH-001] state-changing calls carry the session's CSRF token and the chosen workspace", async () => {
    const f = vi.fn(async () => new Response(JSON.stringify({ id: "s" }), { status: 200 }));
    vi.stubGlobal("fetch", f);
    setCsrf("csrf-1");
    setWorkspace("ws-b");
    await api.capabilities();
    const [, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    const h = new Headers(init.headers);
    expect(h.get("X-CSRF-Token")).toBe("csrf-1");
    expect(h.get("X-OPA-Workspace")).toBe("ws-b");
    expect(h.get("X-OPA-UI")).toBe("1");
    expect(window.localStorage.getItem("opa.workspace")).toBe("ws-b");
  });

  it("[MU-AUTH-001] an ended session sends the person to sign in and back", async () => {
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, pathname: "/review", search: "?job=j1", assign });
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ code: "AUTH_REQUIRED" }), { status: 401 })));
    await expect(api.capabilities()).rejects.toThrow("HTTP 401");
    expect(assign).toHaveBeenCalledWith("/login?next=%2Freview%3Fjob%3Dj1");
  });

  it("[MU-AUTH-001] the desktop app never sends a workspace or CSRF header it was not given", async () => {
    const f = vi.fn(async () => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", f);
    await api.capabilities();
    const h = new Headers((f.mock.calls[0] as unknown as [string, RequestInit])[1].headers);
    expect(h.get("X-CSRF-Token")).toBeNull();
    expect(h.get("X-OPA-Workspace")).toBeNull();
  });
});
