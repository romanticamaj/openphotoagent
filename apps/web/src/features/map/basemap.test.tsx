import { fireEvent, render, screen } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../../lib/i18n";
import { BasemapBanner, basemapOfferDismissed, basemapStyle, dismissBasemapOffer } from "./basemap";

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
});
