import { fireEvent, render, screen } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../../lib/i18n";
import { BasemapBanner, basemapOfferDismissed, basemapStyle, dismissBasemapOffer, mapMaxZoom, selectionStale } from "./basemap";

beforeAll(async () => {
  await i18n.changeLanguage("zh-TW");
});

describe("offline street map (ADR-0037)", () => {
  it("[MAP-017] 有街道圖時：Protomaps 圖層、中文地名、本機字型、OpenStreetMap 標示", () => {
    const st = basemapStyle("pmtiles://http://x/api/v1/map/basemap.pmtiles", true, "http://x/map-fonts/{fontstack}/{range}.pbf");
    const src = st.sources.protomaps as { type: string; url: string; attribution: string };
    expect(src.type).toBe("vector");
    expect(src.url).toBe("pmtiles://http://x/api/v1/map/basemap.pmtiles");
    expect(src.attribution).toContain("OpenStreetMap");
    expect(st.glyphs).toBe("http://x/map-fonts/{fontstack}/{range}.pbf");
    expect(st.layers.length).toBeGreaterThan(30);
    expect(JSON.stringify(st.layers)).toContain("name:zh-Hant");
  });

  it("[MAP-017] 還沒有街道圖：一個按鈕下載；下載中說正在下載；有了就不出現", () => {
    const go = vi.fn();
    const { rerender, container } = render(<BasemapBanner status={{ region: "taiwan", available: false, bytes: 0, operation_id: null }} onDownload={go} />);
    fireEvent.click(screen.getByRole("button", { name: /下載台灣街道圖/ }));
    expect(go).toHaveBeenCalled();
    rerender(<BasemapBanner status={{ region: "taiwan", available: false, bytes: 0, operation_id: "op1" }} onDownload={go} />);
    expect(screen.queryByRole("button", { name: /下載台灣街道圖/ })).toBeNull();
    expect(screen.getByTestId("basemap-banner").textContent).toContain("正在下載");
    rerender(<BasemapBanner status={{ region: "taiwan", available: true, bytes: 9, operation_id: null }} onDownload={go} />);
    expect(container.querySelector("[data-testid=basemap-banner]")).toBeNull();
  });

  it("[MAP-018] 回歸：「不用了」收起提示並記住；說的是國界與海岸線（驗證第 12 輪）", () => {
    const go = vi.fn();
    const no = vi.fn();
    render(<BasemapBanner status={{ region: "taiwan", available: false, bytes: 0, operation_id: null }} onDownload={go} onDismiss={no} />);
    expect(screen.getByTestId("basemap-banner").textContent).toContain("國界與海岸線");
    fireEvent.click(screen.getByRole("button", { name: "不用了" }));
    expect(no).toHaveBeenCalled();
    dismissBasemapOffer();
    expect(basemapOfferDismissed()).toBe(true);
  });

  it("[MAP-019] 回歸：按過「不用了」之後仍留一個小按鈕可以下載（驗證第 23 輪：提示再也回不來）", () => {
    const go = vi.fn();
    render(<BasemapBanner status={{ region: "taiwan", available: false, bytes: 0, operation_id: null }} onDownload={go} dismissed />);
    expect(screen.getByTestId("basemap-banner").textContent).not.toContain("國界與海岸線");
    fireEvent.click(screen.getByRole("button", { name: /下載台灣街道圖/ }));
    expect(go).toHaveBeenCalled();
  });

  it("[MAP-020] 回歸：沒有圖示也不報錯；範圍外是海與國界、不是灰色方框；放大有上限（驗證第 23 輪）", () => {
    const land = { type: "FeatureCollection" as const, features: [] };
    const st = basemapStyle("pmtiles://x", false, "g", land);
    expect(JSON.stringify(st.layers)).not.toContain("icon-image"); // no sprite ships: ~30 「could not be loaded」
    const bg = st.layers[0] as unknown as { type: string; paint: Record<string, string> };
    expect(bg.type).toBe("background");
    expect(bg.paint["background-color"]).not.toBe("#cccccc");
    expect(st.layers[1]).toMatchObject({ type: "fill", source: "countries" }); // land outside the extract
    expect((st.sources.countries as { data: unknown }).data).toBe(land);
    expect(mapMaxZoom(true)).toBe(18);
    expect(mapMaxZoom(false)).toBeLessThanOrEqual(10);
  });
});

describe("the street map zoomed out (MAP-021, MAP-022)", () => {
  it("[MAP-021] 回歸：縮小到整個東亞時只畫國界與陸地，看不到街道圖範圍的直線（驗證第 24 輪）", () => {
    const land = { type: "FeatureCollection" as const, features: [] };
    const st = basemapStyle("pmtiles://x", false, "g", land);
    const street = st.layers.filter((l) => "source" in l && l.source === "protomaps");
    expect(street.length).toBeGreaterThan(30);
    for (const l of street) expect(l.minzoom ?? 0).toBeGreaterThanOrEqual(5);
    expect(st.layers.find((l) => l.id === "ne-border")).toMatchObject({ type: "line", source: "countries" });
  });

  it("[MAP-022] 回歸：選了一群照片後縮小超過一級，下面的說明收起（驗證第 24 輪：還寫著「這 35 張都拍在約 30 公尺」）", () => {
    expect(selectionStale(14, 14)).toBe(false);
    expect(selectionStale(14, 13.5)).toBe(false);
    expect(selectionStale(14, 12.9)).toBe(true);
    expect(selectionStale(14, 16)).toBe(false); // zooming in keeps it
  });
});

