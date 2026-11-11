// Offline street map (10 §2.5, ADR-0037): an OpenStreetMap extract of Taiwan as one PMTiles file in the data folder,
// read in byte ranges from the local API (never from the internet), drawn with the Protomaps style in Chinese.
// Latin glyphs ship with the app (public/map-fonts, OFL); Chinese uses the system font (localIdeographFontFamily).
import { layers, namedFlavor } from "@protomaps/basemaps";
import { addProtocol, type LayerSpecification, type StyleSpecification } from "maplibre-gl";
import { FetchSource, PMTiles, Protocol } from "pmtiles";
import { useTranslation } from "react-i18next";
import { Button } from "../../components/Button";
import { endpoint } from "../../lib/platform";

export type BasemapStatus = { region: string; available: boolean; bytes: number; operation_id: string | null };

/** Closest zoom: past the extract's deepest tiles the street map is unlabelled blocks (MAP-020); borders only, a blank fill. */
export function mapMaxZoom(streets: boolean): number {
  return streets ? 18 : 9;
}

/** No icon sprite ships, so text and lines only (MAP-020): icons were ~30 「could not be loaded」 and blank shields. */
function withoutIcons(l: LayerSpecification): LayerSpecification {
  if (l.type !== "symbol" || !l.layout || !("icon-image" in l.layout)) return l;
  const layout = Object.fromEntries(Object.entries(l.layout).filter(([k]) => !k.startsWith("icon-")));
  return { ...l, layout } as LayerSpecification;
}

export function basemapStyle(source: string, dark: boolean, glyphs: string, land?: GeoJSON.GeoJSON): StyleSpecification {
  const flavor = namedFlavor(dark ? "dark" : "light");
  const all = (layers("protomaps", flavor, { lang: "zh-Hant" }) as LayerSpecification[]).map(withoutIcons);
  // outside the Taiwan extract: sea, with the bundled borders' land under it, not a grey rectangle (MAP-020)
  const [bg, ...rest] = all;
  const sea = { ...bg, paint: { "background-color": flavor.water } } as LayerSpecification;
  const under: LayerSpecification[] = land ? [{ id: "ne-land", type: "fill", source: "countries", paint: { "fill-color": flavor.earth } }] : [];
  return {
    version: 8,
    glyphs,
    sources: {
      ...(land ? { countries: { type: "geojson" as const, data: land } } : {}),
      protomaps: {
        type: "vector",
        url: source,
        attribution: '<a href="https://www.openstreetmap.org/copyright">© OpenStreetMap</a> · Protomaps',
      },
    },
    layers: [sea, ...under, ...rest],
  };
}

let protocol: Protocol | null = null;

/** The basemap's `pmtiles://` URL, with the local API's credentials (desktop launch token) on every range read. */
export async function basemapSource(): Promise<string> {
  const { url, headers } = await endpoint("/api/v1/map/basemap.pmtiles");
  if (!protocol) {
    protocol = new Protocol();
    addProtocol("pmtiles", protocol.tile);
  }
  if (!protocol.get(url)) protocol.add(new PMTiles(new FetchSource(url, new Headers(headers))));
  return `pmtiles://${url}`;
}

export function glyphsUrl(): string {
  return `${window.location.origin}/map-fonts/{fontstack}/{range}.pbf`;
}

const DISMISS_KEY = "opa.basemap.offer.dismissed";

/** 「不用了」 is remembered on this computer only (MAP-018); storage may be unavailable, so every access is guarded. */
export function basemapOfferDismissed(): boolean {
  try {
    return window.localStorage.getItem(DISMISS_KEY) === "1";
  } catch {
    return false;
  }
}

export function dismissBasemapOffer(): void {
  try {
    window.localStorage.setItem(DISMISS_KEY, "1");
  } catch {
    /* not remembered: it comes back next time */
  }
}

/** No street map yet: one button (the worker downloads and extracts it); while it runs, say so. */
export function BasemapBanner({
  status,
  onDownload,
  onDismiss,
  dismissed,
}: {
  status: BasemapStatus;
  onDownload: () => void;
  onDismiss?: () => void;
  /** after 「不用了」: just the button, so the offer can come back (MAP-019) */
  dismissed?: boolean;
}) {
  const { t } = useTranslation();
  if (status.available) return null;
  if (dismissed && !status.operation_id)
    return (
      <div data-testid="basemap-banner" className="text-sm">
        <button type="button" className="text-sm text-[var(--text-muted)] underline" onClick={onDownload}>
          {t("map.basemapDownload")}
        </button>
      </div>
    );
  return (
    <div data-testid="basemap-banner" className="flex flex-wrap items-center gap-3 rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--surface)] p-3 text-sm">
      {status.operation_id ? (
        <span>{t("map.basemapDownloading")}</span>
      ) : (
        <>
          <span>{t("map.basemapOffer")}</span>
          <Button onClick={onDownload}>{t("map.basemapDownload")}</Button>
          {onDismiss ? (
            <button type="button" className="text-sm underline" onClick={onDismiss}>
              {t("map.basemapNoThanks")}
            </button>
          ) : null}
        </>
      )}
    </div>
  );
}
