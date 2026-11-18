// Map (10 §2.5): MapLibre in no-tile mode — country borders from Natural Earth 110m (public domain, via
// world-atlas) bundled with this page, so the map works offline and never requests external tiles (MAP-001). With the
// offline street map downloaded (ADR-0037) the style switches to it: streets and place names, still all local.
// Photos with GPS are grid-clustered (cluster.ts); clicking a cluster shows that area's photos (MAP-002).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Map as MapLibreMap, Marker } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import "./worker";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { feature } from "topojson-client";
import countries110 from "world-atlas/countries-110m.json";
import { api, mediaUrl } from "../../lib/api";
import { BasemapBanner, basemapOfferDismissed, basemapSource, basemapStyle, dismissBasemapOffer, glyphsUrl, mapMaxZoom, selectionStale } from "./basemap";
import { endpoint } from "../../lib/platform";
import { clusterPoints, type Cluster, type GeoPoint } from "./cluster";
import { boundsOf, unwrapRings, spanMeters } from "./geo";

const DARK = { sea: "#0d0f12", land: "#2c3138", border: "#5c6570" };
const LIGHT = { sea: "#dfe7ee", land: "#f7f5f0", border: "#9aa4ad" };

function isDark(): boolean {
  const t = document.documentElement.dataset.theme;
  return t ? t === "dark" : !window.matchMedia?.("(prefers-color-scheme: light)").matches;
}

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
  const qc = useQueryClient();
  const bm = useQuery({
    queryKey: ["basemap"],
    queryFn: api.basemap,
    refetchInterval: (x) => (x.state.data?.operation_id ? 3000 : false), // while it downloads
  });
  const getMap = useMutation({ mutationFn: api.downloadBasemap, onSettled: () => void qc.invalidateQueries({ queryKey: ["basemap"] }) });
  const streets = Boolean(bm.data?.available);
  const [offerOff, setOfferOff] = useState(basemapOfferDismissed);
  const box = useRef<HTMLDivElement>(null);
  const map = useRef<MapLibreMap | null>(null);
  const [zoom, setZoom] = useState(1);
  const [picked, setPicked] = useState<Cluster | null>(null);
  const pickedAt = useRef(0); // the closest zoom since the pick: the map zooms in after it, then the person may zoom out
  // the note described a group no longer shown once the map is zoomed out past it (MAP-022)
  useEffect(() => {
    if (!picked) return;
    if (zoom > pickedAt.current) pickedAt.current = zoom; // still flying in: the zoom before it was the old one (MAP-002)
    else if (selectionStale(pickedAt.current, zoom)) setPicked(null);
  }, [zoom, picked]);
  const clusters = useMemo(() => clusterPoints(q.data?.points ?? [], zoom), [q.data, zoom]);
  const hashOf = useMemo(() => new Map((q.data?.points ?? []).map((p) => [p.id, p.ch ?? null])), [q.data]);

  useEffect(() => {
    if (!box.current) return;
    const palette = isDark() ? DARK : LIGHT; // a light basemap sat in the dark app (audit round 3)
    const m = new MapLibreMap({
      container: box.current,
      style: {
        version: 8,
        sources: { countries: { type: "geojson", data: unwrapRings(borders) } },
        layers: [
          { id: "bg", type: "background", paint: { "background-color": palette.sea } },
          { id: "land", type: "fill", source: "countries", paint: { "fill-color": palette.land } },
          { id: "border", type: "line", source: "countries", paint: { "line-color": palette.border, "line-width": 0.6 } },
        ],
      },
      center: [120, 25],
      zoom: 1,
      maxZoom: mapMaxZoom(false),
      attributionControl: { compact: true }, // OpenStreetMap credit when the street map is on (ODbL)
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

  // the street map, once it is here (ADR-0037)
  useEffect(() => {
    const m = map.current;
    if (!m || !streets) return;
    let live = true;
    void basemapSource().then((src) => {
      if (!live) return;
      m.setMaxZoom(mapMaxZoom(true));
      m.setStyle(basemapStyle(src, isDark(), glyphsUrl(), unwrapRings(borders)));
    });
    return () => {
      live = false;
    };
  }, [streets]);

  // the first view is where the photos are, not the whole world (MAP-013)
  const fitted = useRef(false);
  useEffect(() => {
    const m = map.current;
    const b = boundsOf(q.data?.points ?? []);
    if (!m || !b || fitted.current) return;
    fitted.current = true;
    m.fitBounds(b, { padding: 60, maxZoom: streets ? 14 : 6, duration: 0 }); // borders only: closer is a blank fill
  }, [q.data, streets]);

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
      el.addEventListener("click", () => {
        setPicked(c);
        pickedAt.current = m.getZoom();
        const [w, so, e, n] = c.bbox;
        if (c.count > 1 && (e > w || n > so)) {
          const fit = { padding: 80, maxZoom: streets ? 18 : 9 };
          m.fitBounds([[w, so], [e, n]], fit); // drill down
        }
      });
      return new Marker({ element: el }).setLngLat([c.lon, c.lat]).addTo(m);
    });
    return () => markers.forEach((mk) => mk.remove());
  }, [clusters, t, streets]);

  return (
    <section className="flex h-full flex-col gap-3 p-4" aria-labelledby="map-h">
      <h1 id="map-h" className="text-xl font-semibold">
        {t("map.title")}
      </h1>
      {bm.data ? (
        <BasemapBanner
          status={bm.data}
          dismissed={offerOff}
          onDownload={() => getMap.mutate()}
          onDismiss={() => {
            dismissBasemapOffer();
            setOfferOff(true);
          }}
        />
      ) : null}
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
          <p className="text-sm">
            {picked.count > 1 && spanMeters(picked.bbox) < 1000
              ? t("map.oneSpot", { n: picked.count.toLocaleString(), m: Math.max(10, Math.round(spanMeters(picked.bbox) / 10) * 10) })
              : t("map.inArea", { n: picked.count.toLocaleString() })}
          </p>
          <ul className="grid grid-cols-[repeat(auto-fill,minmax(96px,1fr))] gap-1">
            {picked.ids.map((id) => (
              <li key={id}>
                <img src={mediaUrl(id, "t256", hashOf.get(id))} alt={t("map.photo")} className="aspect-square w-full object-cover" />
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
