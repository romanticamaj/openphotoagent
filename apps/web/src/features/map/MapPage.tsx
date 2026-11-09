// Map (10 §2.5): MapLibre in no-tile mode — country borders from Natural Earth 110m (public domain, via
// world-atlas) bundled with this page, so the map works offline and never requests external tiles (MAP-001).
// Photos with GPS are grid-clustered (cluster.ts); clicking a cluster shows that area's photos (MAP-002).
import { useQuery } from "@tanstack/react-query";
import { Map as MapLibreMap, Marker } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import "./worker";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { feature } from "topojson-client";
import countries110 from "world-atlas/countries-110m.json";
import { mediaUrl } from "../../lib/api";
import { endpoint } from "../../lib/platform";
import { clusterPoints, type Cluster, type GeoPoint } from "./cluster";

type Points = { points: GeoPoint[]; without_location: number };

async function fetchPoints(): Promise<Points> {
  const { url, headers } = await endpoint("/api/v1/map/points");
  const r = await fetch(url, { headers });
  return (await r.json()) as Points;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const borders = feature(countries110 as any, (countries110 as any).objects.countries) as unknown as GeoJSON.FeatureCollection;

export function MapPage() {
  const { t } = useTranslation();
  const q = useQuery({ queryKey: ["map-points"], queryFn: fetchPoints });
  const box = useRef<HTMLDivElement>(null);
  const map = useRef<MapLibreMap | null>(null);
  const [zoom, setZoom] = useState(1);
  const [picked, setPicked] = useState<Cluster | null>(null);
  const clusters = useMemo(() => clusterPoints(q.data?.points ?? [], zoom), [q.data, zoom]);

  useEffect(() => {
    if (!box.current) return;
    const m = new MapLibreMap({
      container: box.current,
      style: {
        version: 8,
        sources: { countries: { type: "geojson", data: borders } },
        layers: [
          { id: "bg", type: "background", paint: { "background-color": "#dfe7ee" } },
          { id: "land", type: "fill", source: "countries", paint: { "fill-color": "#f7f5f0" } },
          { id: "border", type: "line", source: "countries", paint: { "line-color": "#9aa4ad", "line-width": 0.6 } },
        ],
      },
      center: [120, 25],
      zoom: 1,
      attributionControl: false,
    });
    m.on("zoomend", () => setZoom(m.getZoom()));
    map.current = m;
    const ro = new ResizeObserver(() => m.resize()); // the page lays out after the map starts
    ro.observe(box.current);
    return () => {
      ro.disconnect();
      m.remove();
    };
  }, []);

  useEffect(() => {
    const m = map.current;
    if (!m) return;
    const markers = clusters.map((c) => {
      const el = document.createElement("button");
      el.type = "button";
      el.className = "map-cluster";
      el.textContent = c.count.toLocaleString();
      el.setAttribute("aria-label", t("map.cluster", { n: c.count.toLocaleString() }));
      el.dataset.count = String(c.count);
      el.addEventListener("click", () => setPicked(c));
      return new Marker({ element: el }).setLngLat([c.lon, c.lat]).addTo(m);
    });
    return () => markers.forEach((mk) => mk.remove());
  }, [clusters, t]);

  return (
    <section className="flex h-full flex-col gap-3 p-4" aria-labelledby="map-h">
      <h1 id="map-h" className="text-xl font-semibold">
        {t("map.title")}
      </h1>
      <p data-testid="map-no-location" className="text-sm text-[var(--text-muted)]">
        {t("map.noLocation", { n: (q.data?.without_location ?? 0).toLocaleString() })}
      </p>
      <div role="region" aria-label={t("map.label")} className="relative min-h-[420px] flex-1 overflow-hidden rounded-[var(--radius-md)] border border-[var(--border)]">
        {/* inline: maplibre-gl.css gives .maplibregl-map position: relative, which beat the utility class and left the
            map 0 px tall and clipped (MAP-011) */}
        <div ref={box} style={{ position: "absolute", inset: 0 }} />
        <span data-testid="map-borders" data-countries={borders.features.length} hidden />
      </div>
      {picked ? (
        <div data-testid="map-selection" className="space-y-2">
          <p className="text-sm">{t("map.inArea", { n: picked.count.toLocaleString() })}</p>
          <ul className="grid grid-cols-[repeat(auto-fill,minmax(96px,1fr))] gap-1">
            {picked.ids.map((id) => (
              <li key={id}>
                <img src={mediaUrl(id, "t256")} alt={t("map.photo")} className="aspect-square w-full object-cover" />
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
