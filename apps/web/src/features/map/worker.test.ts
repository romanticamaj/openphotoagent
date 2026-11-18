import { describe, expect, it, vi } from "vitest";

const set = vi.hoisted(() => vi.fn());
vi.mock("maplibre-gl", () => ({ setWorkerUrl: set, Map: class {}, Marker: class {} }));

describe("the map's worker", () => {
  it("[MAP-010] 回歸：地圖的 worker 從 app 自己載入（安裝版地圖空白）", async () => {
    // Vite does not emit maplibre's default worker next to the chunk, so the installed app's map stayed blank with
    // "Worker failed to load". The bundled file is same-origin, which the desktop CSP (default-src 'self') allows.
    await import("./worker");
    expect(set).toHaveBeenCalledTimes(1);
    const url = String(set.mock.calls[0][0]);
    expect(url).toMatch(/maplibre-gl-worker/);
    expect(url).not.toMatch(/^(blob:|https?:\/\/(?!localhost))/);
  });
});
