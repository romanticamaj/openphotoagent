// Lightbox (10 §3): progressive ThumbHash → t512 → p1024, neighbours preloaded, zoom/pan, filmstrip, `I` info
// panel with EXIF and every AI label's model and decision layer, inline correction (→ review), reveal in file
// manager on desktop only. role="dialog" aria-modal; position is announced through aria-live.
import { AddToAlbum, FavoriteButton, typingIn, useFavorite } from "../albums/AlbumButtons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { FaceCrop } from "../../components/FaceCrop";
import { FaceBoxes } from "./faceBoxes";
import { Suspense, lazy, useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { turnStyle } from "../../lib/turn";
import { api, mediaUrl, preparePreview, type AssetInfo, type TimelineAsset } from "../../lib/api";
import { platform } from "../../lib/platform";
import { placeholder } from "../../lib/thumbhash";
import type { GridApi } from "./PhotoGrid";

// 「問這張」 loads with the ask chunk, not with the photos route (PERF-003)
const AskPhoto = lazy(() => import("../ask/AskPanel").then((m) => ({ default: m.AskPhoto })));

const FILM = 7;

export function Lightbox({ start, grid, onClose }: { start: number; grid: GridApi; onClose: (assetId: string | null) => void }) {
  const { t, i18n } = useTranslation();
  const [ord, setOrd] = useState(start);
  const [asset, setAsset] = useState<TimelineAsset | null>(null);
  const [assetOrd, setAssetOrd] = useState<number | null>(null); // which photo `asset` is: it arrives after ord moves
  const [favWanted, setFavWanted] = useState<number | null>(null); // F pressed before that photo was in (COL-013)
  const [film, setFilm] = useState<Array<{ ord: number; a: TimelineAsset }>>([]);
  const [stage, setStage] = useState<"t512" | "p1024" | "p2048" | null>(null);
  const [sharp, setSharp] = useState(false); // p2048 is there to show (VIEW-035)
  const [scale, setScale] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [showInfo, setShowInfo] = useState(false);
  const [natural, setNatural] = useState<{ w: number; h: number } | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const dialog = useRef<HTMLDivElement>(null);
  const opener = useRef<Element | null>(document.activeElement);

  useEffect(() => {
    let live = true;
    setStage(null);
    setSharp(false);
    setScale(1);
    setPan({ x: 0, y: 0 });
    void grid.at(ord).then((r) => {
      if (!live) return;
      setAsset(r?.asset ?? null);
      setAssetOrd(ord);
    });
    const lo = Math.max(0, ord - FILM);
    const hi = Math.min(grid.total - 1, ord + FILM);
    void Promise.all(Array.from({ length: hi - lo + 1 }, (_, i) => grid.at(lo + i).then((r) => (r ? { ord: lo + i, a: r.asset } : null)))).then(
      (xs) => live && setFilm(xs.filter((x): x is { ord: number; a: TimelineAsset } => x !== null)),
    );
    return () => {
      live = false;
    };
  }, [ord, grid]);

  // preload neighbours' first sharp frame (10 §3.4)
  useEffect(() => {
    for (const f of film) {
      if (Math.abs(f.ord - ord) === 1) {
        const im = new Image();
        im.src = mediaUrl(f.a.id, "t512", f.a.ch);
      }
    }
  }, [film, ord]);

  useEffect(() => {
    dialog.current?.focus();
    const prev = opener.current;
    return () => (prev as HTMLElement | null)?.focus?.();
  }, []);

  // turned in the app (ROT-003): shown at once, kept in the library, the file is not touched
  const qcr = useQueryClient();
  const [turns, setTurns] = useState<Record<string, number>>({});
  const rot = asset ? (turns[asset.id] ?? asset.rot ?? 0) : 0;
  const rotate = useCallback(() => {
    if (!asset) return;
    const next = (rot + 90) % 360;
    setTurns((m) => ({ ...m, [asset.id]: next }));
    void api.setRotation(asset.id, next).then(() => {
      void qcr.invalidateQueries({ queryKey: ["timeline"] });
      void qcr.invalidateQueries({ queryKey: ["search"] });
      void qcr.invalidateQueries({ queryKey: ["on-this-day"] }); // its thumbnails too (ROT-004)
      void qcr.invalidateQueries({ queryKey: ["album"] });
      void qcr.invalidateQueries({ queryKey: ["asset", asset.id] }); // 尺寸 follows the turn (ROT-005)
    });
  }, [asset, rot, qcr]);
  const stageRef = useRef<HTMLDivElement>(null);
  const [stageBox, setStageBox] = useState<{ w: number; h: number }>({ w: 0, h: 0 });
  useEffect(() => {
    const el = stageRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => setStageBox({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const view = `translate(${pan.x}px, ${pan.y}px) scale(${scale})`;
  const quarter = rot === 90 || rot === 270;
  const imgStyle: React.CSSProperties = quarter
    ? {
        position: "absolute",
        left: "50%",
        top: "50%",
        width: `${stageBox.h}px`,
        height: `${stageBox.w}px`,
        maxWidth: "none",
        transform: `translate(-50%, -50%) ${view} rotate(${rot}deg)`,
      }
    : { transform: `${view}${rot ? ` rotate(${rot}deg)` : ""}`, width: "100%", height: "100%" };

  // zoomed in: have the 2048 px preview made, then show it over the 1024 one (VIEW-035, 10 §3.4)
  useEffect(() => {
    if (!asset || scale <= 1 || sharp) return;
    let live = true;
    void preparePreview(asset.id).then((ok) => live && ok && setSharp(true));
    return () => {
      live = false;
    };
  }, [asset, scale, sharp]);

  const close = useCallback(() => onClose(asset?.id ?? null), [asset, onClose]);
  const go = useCallback((d: number) => setOrd((o) => Math.max(0, Math.min(grid.total - 1, o + d))), [grid.total]);
  // a slideshow (VIEW-039): the next photo every SLIDE_MS, stopping on the last; moving by hand stops it
  const [playing, setPlaying] = useState(false);
  const [showKeys, setShowKeys] = useState(false);
  const step = (d: number) => {
    setPlaying(false);
    go(d);
  };
  useEffect(() => {
    if (!playing) return;
    const id = window.setTimeout(() => (ord >= grid.total - 1 ? setPlaying(false) : setOrd(ord + 1)), SLIDE_MS);
    return () => window.clearTimeout(id);
  }, [playing, ord, grid.total]);

  const onKey = (e: React.KeyboardEvent) => {
    if (typingIn(e.target)) return; // text in a field, not a shortcut (COL-008)
    if ((e.key === "a" || e.key === "A") && (e.ctrlKey || e.metaKey)) {
      e.preventDefault(); // not the page's text behind the photo (audit round 54)
      return;
    }
    const k: Record<string, () => void> = {
      Escape: close,
      ArrowLeft: () => step(-1),
      ArrowRight: () => step(1),
      " ": () => setPlaying((x) => !x),
      "?": () => setShowKeys(true),
      i: () => setShowInfo((s) => !s),
      I: () => setShowInfo((s) => !s),
      r: rotate,
      R: rotate,
      f: () => setFavWanted(ord), // carried out once this photo and its ★ state are in, not dropped
      F: () => setFavWanted(ord),
      "+": () => setScale((s) => Math.min(8, s * 1.5)),
      "=": () => setScale((s) => Math.min(8, s * 1.5)),
      "-": () => setScale((s) => Math.max(1, s / 1.5)),
      "0": () => {
        setScale(1);
        setPan({ x: 0, y: 0 });
      },
    };
    const f = k[e.key];
    if (f) {
      e.preventDefault();
      f();
    }
    if (e.key === "Tab") trapFocus(e, dialog.current);
  };

  const dragFrom = useRef<{ x: number; y: number; px: number; py: number } | null>(null);
  const ph = placeholder(asset?.thumbhash);
  const position = t("lightbox.position", { n: (ord + 1).toLocaleString(i18n.language), total: grid.total.toLocaleString(i18n.language) });

  return (
    <div ref={dialog} role="dialog" aria-modal="true" aria-label={t("lightbox.label")} tabIndex={-1} className="lightbox" onKeyDown={onKey} data-testid="lightbox">
      {showKeys ? <KeyList onClose={() => (setShowKeys(false), dialog.current?.focus())} /> : null}
      <div className="col-span-1 flex items-center gap-2 px-3">
        <button type="button" onClick={close} className="rounded-[var(--radius-sm)] px-2 py-1 hover:bg-[var(--surface-2)]">{t("lightbox.close")}</button>
        <span aria-live="polite" className="tnum text-[var(--text)]" data-testid="lightbox-position">{position}</span>
        <span className="flex-1" />
        <button type="button" onClick={() => setScale((s) => Math.max(1, s / 1.5))} aria-label={t("lightbox.zoomOut")} className="px-2">−</button>
        <button type="button" onClick={() => setScale((s) => Math.min(8, s * 1.5))} aria-label={t("lightbox.zoomIn")} className="px-2">+</button>
        {asset ? (
          <FavoriteHotkey
            assetId={asset.id}
            wanted={favWanted !== null && favWanted === ord && assetOrd === ord}
            done={() => setFavWanted(null)}
          />
        ) : null}
        {asset ? <FavoriteButton assetId={asset.id} /> : null}
        {asset ? <AddToAlbum assetIds={[asset.id]} /> : null}
        {asset ? (
          <button type="button" onClick={rotate} aria-label={t("lightbox.rotate")} title={`${t("lightbox.rotate")} (R)`} className="icon-button">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M20 12a8 8 0 1 1-2.34-5.66M20 4v4h-4" />
            </svg>
          </button>
        ) : null}
        <button
          type="button"
          aria-pressed={playing}
          aria-label={t(playing ? "lightbox.slideshowStop" : "lightbox.slideshow")}
          title={`${t(playing ? "lightbox.slideshowStop" : "lightbox.slideshow")} (${t("lightbox.k.space")})`}
          onClick={() => setPlaying((x) => !x)}
          className="icon-button"
        >
          <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor" aria-hidden="true">
            {playing ? <path d="M7 5h3.5v14H7zM13.5 5H17v14h-3.5z" /> : <path d="M8 5.5v13l10.5-6.5z" />}
          </svg>
        </button>
        <button type="button" aria-label={t("lightbox.keys")} title={`${t("lightbox.keys")} (?)`} onClick={() => setShowKeys(true)} className="icon-button text-sm font-semibold">
          ?
        </button>
        <button type="button" aria-pressed={showInfo} onClick={() => setShowInfo((s) => !s)} className="rounded-[var(--radius-sm)] px-2 py-1 hover:bg-[var(--surface-2)]">{t("lightbox.info")}</button>
      </div>
      <div
        ref={stageRef}
        className="stage"
        style={{ backgroundImage: ph ? `url(${ph})` : undefined, backgroundSize: "contain", backgroundRepeat: "no-repeat", backgroundPosition: "center" }}
        onWheel={(e) => setScale((s) => Math.max(1, Math.min(8, s * (e.deltaY < 0 ? 1.15 : 1 / 1.15))))}
        onDoubleClick={() => setScale((s) => (s > 1 ? 1 : 2))}
        onPointerDown={(e) => {
          if (scale > 1) dragFrom.current = { x: e.clientX, y: e.clientY, px: pan.x, py: pan.y };
        }}
        onPointerMove={(e) => {
          const d = dragFrom.current;
          if (d) setPan({ x: d.px + e.clientX - d.x, y: d.py + e.clientY - d.y });
        }}
        onPointerUp={() => (dragFrom.current = null)}
        data-testid="lightbox-stage"
        data-scale={scale}
      >
        {asset && (
          <>
            <img
              key={`${asset.id}-t512`}
              src={mediaUrl(asset.id, "t512", asset.ch)}
              alt=""
              data-variant="t512"
              onLoad={(e) => {
                setStage((s) => s ?? "t512");
                setNatural({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight });
              }}
              style={imgStyle}
            />
            {stage !== null && (
              <img
                key={`${asset.id}-p1024`}
                src={mediaUrl(asset.id, "p1024", asset.ch)}
                alt={asset.taken_at ? t("viewer.photoAlt", { date: asset.taken_at.slice(0, 10) }) : ""}
                data-variant="p1024"
                onLoad={() => setStage((s) => (s === "p2048" ? s : "p1024"))}
                style={imgStyle}
              />
            )}
            {sharp ? (
              // zoomed in: the 2048 px preview, made on first request (10 §3.4); the 1024 one stays under it until then
              <img
                key={`${asset.id}-p2048`}
                src={mediaUrl(asset.id, "p2048", asset.ch)}
                alt=""
                data-variant="p2048"
                onLoad={() => setStage("p2048")}
                onError={(e) => (e.currentTarget.style.visibility = "hidden")}
                style={imgStyle}
              />
            ) : null}
          </>
        )}
        {asset && showInfo && natural && scale === 1 && !rot ? <PhotoFaces id={asset.id} photo={natural} /> : null}
        {grid.total > 1 ? (
          <>
            <button type="button" onClick={() => step(-1)} aria-label={t("lightbox.prev")} className="absolute left-2 rounded-full bg-black/40 px-3 py-2">‹</button>
            <button type="button" onClick={() => step(1)} aria-label={t("lightbox.next")} className="absolute right-2 rounded-full bg-black/40 px-3 py-2">›</button>
          </>
        ) : null}
      </div>
      <nav className="filmstrip" aria-label={t("lightbox.filmstrip")}>
        {film.map((f) => (
          <button
            key={f.a.id}
            type="button"
            aria-current={f.ord === ord}
            aria-label={t("lightbox.position", { n: f.ord + 1, total: grid.total })}
            onClick={() => setOrd(f.ord)}
            style={{
              backgroundImage: `url(${mediaUrl(f.a.id, "t256", f.a.ch)}), url(${placeholder(f.a.thumbhash) ?? ""})`,
              ...turnStyle(f.a.id === asset?.id ? rot : f.a.rot), // a square tile: turning is enough (ROT-004)
            }}
          />
        ))}
      </nav>
      {showInfo && asset && <InfoPanel id={asset.id} onSaved={() => setToast(t("lightbox.correctionSaved"))} />}
      {toast && <Toast text={toast} onDone={() => setToast(null)} />}
    </div>
  );
}

function trapFocus(e: React.KeyboardEvent, root: HTMLElement | null) {
  if (!root) return;
  const f = [...root.querySelectorAll<HTMLElement>("button, select, input, textarea, a[href], [tabindex='0']")].filter((x) => !x.hasAttribute("disabled"));
  if (!f.length) return;
  const first = f[0];
  const last = f[f.length - 1];
  const at = document.activeElement as HTMLElement | null;
  if (!at || at === root || !root.contains(at)) {
    // on the dialog itself (as it opens) or outside it: Shift+Tab went to the page behind (A11Y-012)
    e.preventDefault();
    (e.shiftKey ? last : first).focus();
  } else if (e.shiftKey && at === first) {
    e.preventDefault();
    last.focus();
  } else if (!e.shiftKey && at === last) {
    e.preventDefault();
    first.focus();
  }
}

function Toast({ text, onDone }: { text: string; onDone: () => void }) {
  useEffect(() => {
    const h = setTimeout(onDone, 3000);
    return () => clearTimeout(h);
  }, [onDone]);
  return (
    <div role="status" className="toast">
      {text}
    </div>
  );
}

function fmtExposure(s?: number): string | null {
  if (!s) return null;
  return s >= 1 ? `${s}s` : `1/${Math.round(1 / s)}s`;
}

export function InfoPanel({ id, onSaved }: { id: string; onSaved: () => void }) {
  const { t, i18n } = useTranslation();
  const qc = useQueryClient();
  const info = useQuery({ queryKey: ["asset", id, i18n.language], queryFn: () => api.asset(id, i18n.language) });
  const preset = useQuery({ queryKey: ["preset", "by-content-12", i18n.language], queryFn: () => api.preset("by-content-12", i18n.language) });
  const names = new Map((preset.data?.categories ?? []).map((c) => [c.key, c.name]));
  const [pick, setPick] = useState("");
  const save = useMutation({
    mutationFn: () => api.correct(id, pick),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["asset", id] });
      onSaved();
    },
  });
  const a: AssetInfo | undefined = info.data;
  const p = platform();
  if (!a) return <aside className="info" aria-label={t("lightbox.info")}>{t("viewer.loading")}</aside>;
  const ex = a.exif ?? {};
  const camera = [ex.camera_make, ex.camera_model].filter(Boolean).join(" ");
  const exposure = [fmtExposure(ex.exposure_s), ex.f_number ? `f/${ex.f_number}` : null, ex.iso ? `ISO ${ex.iso}` : null, ex.focal_mm ? `${ex.focal_mm} mm` : null]
    .filter(Boolean)
    .join(" · ");
  const rows: Array<[string, string | null]> = [
    [t("lightbox.exif.date"), a.taken_at ? new Date(a.taken_at).toLocaleString(i18n.language) + (a.date_source === "mtime" ? ` (${t("viewer.dateFromMtime")})` : "") : null],
    [t("lightbox.exif.camera"), camera || null],
    [t("lightbox.exif.lens"), ex.lens ?? null],
    [t("lightbox.exif.exposure"), exposure || null],
    // as it is shown: a quarter turn swaps the sides (ROT-004)
    [t("lightbox.exif.size"), a.width && a.height ? (a.rotation === 90 || a.rotation === 270 ? `${a.height} × ${a.width}` : `${a.width} × ${a.height}`) : null],
  ];
  return (
    <aside className="info space-y-4" aria-label={t("lightbox.info")} data-testid="info-panel">
      <section aria-labelledby="ai-h">
        <h2 id="ai-h" className="mb-2 font-semibold">{t("lightbox.ai.title")}</h2>
        {a.human_label && (
          <p data-testid="human-label">
            {t("lightbox.ai.human")}: <strong>{names.get(a.human_label.category) ?? a.human_label.category}</strong>
          </p>
        )}
        {a.labels.length === 0 && <p className="text-[var(--text-muted)]">{t("lightbox.ai.none")}</p>}
        {a.labels[0]?.caption ? <p data-testid="ai-caption" className="mb-2">{a.labels[0].caption}</p> : null}
        {(a.labels[0]?.tags?.length ?? 0) > 0 ? (
          <ul aria-label={t("lightbox.ai.tags")} data-testid="ai-tags" className="mb-3 flex flex-wrap gap-1 text-xs">
            {a.labels[0].tags!.map((g) => (
              <li key={g} className="rounded-[var(--radius-sm)] bg-[var(--surface-2)] px-2 py-0.5">{a.labels[0].tag_names?.[g] ?? g}</li>
            ))}
          </ul>
        ) : null}
        {a.faces_state === "folder_off" ? (
          <p data-testid="faces-folder-off" className="mb-3 text-sm text-[var(--text-muted)]">
            {t("lightbox.facesFolderOff")}{" "}
            <Link to="/settings/faces" className="underline">{t("lightbox.facesSettings")}</Link>
          </p>
        ) : null}
        {(a.people?.length ?? 0) === 0 && (a.faces_found?.total ?? 0) > 0 ? (
          <details data-testid="faces-nobody" className="mb-3 text-sm text-[var(--text-muted)]">
            {/* one short line; why, for whoever asks (VIEW-031) */}
            <summary className="cursor-pointer">{t("lightbox.facesNobodyShort", { n: a.faces_found!.total })}</summary>
            {t(
              a.faces_found!.small === a.faces_found!.total
                ? "lightbox.facesNobodyAllSmall"
                : a.faces_found!.small
                  ? "lightbox.facesNobodySmall"
                  : "lightbox.facesNobody",
              {
                n: a.faces_found!.total,
                small: a.faces_found!.small,
              },
            )}
          </details>
        ) : null}
        <NameFaces
          assetId={id}
          faces={(a.unknown_faces ?? []).filter((u) => !u.small && u.id)}
          small={(a.unknown_faces ?? []).filter((u) => u.small).length}
        />
        {(a.people?.length ?? 0) > 0 ? (
          <div className="mb-3">
            <h3 className="mb-1 text-sm text-[var(--text-muted)]">{t("lightbox.people")}</h3>
            <ul data-testid="photo-people" className="flex flex-wrap gap-1 text-sm">
              {a.people!.map((p) => (
                <li key={p.id}>
                  <Link to="/people" search={{ person: p.id }} className="person-chip" translate="no">
                    {a.people_faces?.[p.id] ? (
                      <FaceCrop assetId={id} ch={a.people_faces[p.id].ch} bbox={a.people_faces[p.id].bbox} className="person-chip-face" />
                    ) : null}
                    <span>{p.name ?? t("faces.unnamed")}</span>
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {a.labels.slice(0, 1).map((l) => (
          <div key={l.result_id} className="text-sm" data-testid="ai-label">
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
              <dt className="text-[var(--text-muted)]">{t("lightbox.ai.category")}</dt>
              <dd data-testid="ai-category">
                {names.get(l.category ?? "") ?? l.category} · {t(`lightbox.decision.${l.decision}`, { defaultValue: l.decision })}
              </dd>
            </dl>
            {/* how the AI decided is for the curious, collapsed (VIEW-030; the fingerprint is 08 §8.3's advanced detail) */}
            <details className="mt-1 text-xs text-[var(--text-muted)]">
              <summary className="cursor-pointer">{t("lightbox.ai.details")}</summary>
              <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
                {l.confidence !== null && (
                  <>
                    <dt>{t("lightbox.ai.confidence")}</dt>
                    <dd className="tnum">{t("common.percent", { n: Math.round(l.confidence * 100) })}</dd>
                  </>
                )}
                <dt>{t("lightbox.ai.layer")}</dt>
                <dd data-testid="ai-layer">{t(`lightbox.layer.${l.source}`, { defaultValue: l.source })}</dd>
                <dt>{t("lightbox.ai.model")}</dt>
                <dd className="break-all font-mono" data-testid="ai-model" translate="no">
                  {l.model_fingerprint}
                </dd>
              </dl>
            </details>
          </div>
        ))}
        {a.labels.length > 1 ? (
          /* earlier analyses (other models or settings) are history, not three opinions at once */
          <details data-testid="ai-history" className="mt-2 text-xs text-[var(--text-muted)]">
            <summary className="cursor-pointer">{t("lightbox.ai.history", { n: a.labels.length - 1 })}</summary>
            <ul className="mt-1 space-y-0.5">
              {a.labels.slice(1).map((l) => (
                <li key={l.result_id}>
                  {names.get(l.category ?? "") ?? l.category}
                  {l.confidence !== null ? ` · ${t("common.percent", { n: Math.round(l.confidence * 100) })}` : ""}
                </li>
              ))}
            </ul>
          </details>
        ) : null}
        {/* a small 「修改…」 chip beside the category, not a form in the middle of the panel (audit round 27) */}
        <form
          className="mt-2 flex items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (pick) save.mutate();
          }}
        >
          <label className="text-sm">
            {/* the name on the control itself: a hidden span was measured as clipped text (I18N-004) */}
            <select aria-label={t("lightbox.correct")} value={pick} onChange={(e) => setPick(e.target.value)} className="facet-chip cursor-pointer text-xs">
              <option value="">{t("lightbox.correctShort")}</option>
              {(preset.data?.categories ?? []).map((c) => (
                <option key={c.key} value={c.key}>
                  {c.name}
                </option>
              ))}
            </select>
          </label>
          {pick ? (
            <button type="submit" className="rounded-full bg-[var(--accent)] px-3 py-0.5 text-xs text-[var(--accent-fg)]">{t("lightbox.correctSave")}</button>
          ) : null}
        </form>
      </section>
      <section aria-labelledby="exif-h">
        <h2 id="exif-h" className="mb-2 font-semibold">{t("lightbox.exif.title")}</h2>
        {/* one icon per fact, as photo apps show them; the label stays for screen readers */}
        <dl className="info-facts text-sm">
          {rows
            .filter(([, v]) => v)
            .map(([k, v]) => (
              <div key={k} className="info-fact">
                {/* icon and a quiet label: a hidden label was measured as clipped (I18N-004) and is read by E2E-L-001 */}
                <dt className="flex items-center gap-2 text-[var(--text-muted)]">
                  <InfoIcon kind={FACT_ICONS[rows.findIndex((r) => r[0] === k)]} />
                  {k}
                </dt>
                <dd className="tnum">{v}</dd>
              </div>
            ))}
        </dl>
      </section>
      <Suspense fallback={null}>
        <AskPhoto assetId={id} />
      </Suspense>
      {(a.locations ?? []).length > 0 && (
        <section aria-labelledby="loc-h">
          <h2 id="loc-h" className="mb-2 font-semibold">{t("lightbox.exif.locations")}</h2>
          <ul className="space-y-1 text-sm">
            {[...new Map((a.locations ?? []).map((l) => [`${l.root_alias}/${l.rel_path}`, l])).values()].map((l) => (
              <li key={`${l.source_id}:${l.rel_path}`} className="flex items-center gap-2">
                <span className="break-all font-mono text-xs">{`${l.root_alias}/${l.rel_path}`}</span>
                {p.kind === "desktop" && p.revealInFileManager && (
                  <button
                    type="button"
                    onClick={() => void p.revealInFileManager!(l.root_alias, l.rel_path)}
                    aria-label={t("lightbox.reveal")}
                    title={t("lightbox.reveal")}
                    className="icon-button shrink-0"
                  >
                    <InfoIcon kind="folder" />
                  </button>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}
    </aside>
  );
}

/** Binds the F key to this photo's ★ (ADR-0041). */
/** F: ★ the photo on screen once its state is in (ADR-0041); a press during → waits for it (COL-013). */
function FavoriteHotkey({ assetId, wanted, done }: { assetId: string; wanted: boolean; done: () => void }) {
  const fav = useFavorite(assetId);
  useEffect(() => {
    if (!wanted || !fav.ready) return;
    fav.toggle();
    done();
  }, [wanted, fav, done]);
  return null;
}

const FACT_ICONS = ["calendar", "camera", "lens", "aperture", "size"] as const;
type IconKind = (typeof FACT_ICONS)[number] | "folder";

/** Line icons for the info panel (24-unit grid, currentColor), drawn inline: no icon font to load. */
function InfoIcon({ kind }: { kind: IconKind }) {
  const d: Record<IconKind, string> = {
    calendar: "M7 3v3M17 3v3M4 9h16M5 5h14a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z",
    camera: "M4 8h3l2-3h6l2 3h3a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1zM12 16.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z",
    lens: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8z",
    aperture: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 3l3 7M21 12l-7 3M12 21l-3-7M3 12l7-3",
    size: "M4 4h6M4 4v6M20 20h-6M20 20v-6M4 4l6 6M20 20l-6-6",
    folder: "M3 6a1 1 0 0 1 1-1h5l2 2h9a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z",
  };
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className="shrink-0 text-[var(--text-muted)]">
      <path d={d[kind]} />
    </svg>
  );
}

/** 「這是誰？」 for the recognisable faces no group took (VIEW-032): one name makes them findable. */
const NAME_ROWS = 4;

function hoverFace(id: string | null) {
  window.dispatchEvent(new CustomEvent("opa:face-hover", { detail: id })); // the box on the photo lights up (VIEW-033)
}

function NameFaces({
  assetId,
  faces,
  small,
}: {
  assetId: string;
  faces: Array<{ id?: string; bbox: number[]; ch?: string | null }>;
  small: number;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [open, setOpen] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [all, setAll] = useState(false); // four rows first: the rest of the panel stays in view (VIEW-034)
  const save = useMutation({
    mutationFn: () => api.nameFace(open!, name.trim()),
    onSuccess: () => {
      setOpen(null);
      setName("");
      void qc.invalidateQueries({ queryKey: ["asset", assetId] });
      void qc.invalidateQueries({ queryKey: ["people"] });
    },
  });
  if (!faces.length) return null;
  return (
    <div className="mb-3">
    <ul className="name-faces" aria-label={t("lightbox.nameFaces")}>
      {(all ? faces : faces.slice(0, NAME_ROWS)).map((f) => (
        <li
          key={f.id}
          data-testid="name-face-row"
          className="name-face-row"
          onMouseEnter={() => hoverFace(f.id ?? null)}
          onMouseLeave={() => hoverFace(null)}
          onFocus={() => hoverFace(f.id ?? null)}
          onBlur={() => hoverFace(null)}
        >
          <FaceCrop assetId={assetId} ch={f.ch ?? null} bbox={f.bbox} className="name-face-crop" />
          {open === f.id ? (
            <form
              className="name-face-form"
              onSubmit={(e) => {
                e.preventDefault();
                if (name.trim()) save.mutate();
              }}
            >
              <input
                aria-label={t("lightbox.faceName")}
                placeholder={t("lightbox.faceNamePlaceholder")}
                autoFocus
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="min-w-0 flex-1 rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--surface)] px-2 py-1 text-sm"
              />
              <button type="submit" className="whitespace-nowrap text-sm underline">
                {t("lightbox.saveFaceName")}
              </button>
            </form>
          ) : (
            <button type="button" className="whitespace-nowrap text-sm underline" onClick={() => setOpen(f.id ?? null)}>
              {t("lightbox.whoIsThis")}
            </button>
          )}
        </li>
      ))}
    </ul>
    {!all && faces.length > NAME_ROWS ? (
      <button type="button" className="mt-1 text-sm underline" onClick={() => setAll(true)}>
        {t("lightbox.showMoreFaces", { n: faces.length - NAME_ROWS })}
      </button>
    ) : null}
    {small ? (
      <p data-testid="name-faces-small" className="mt-1 text-xs text-[var(--text-muted)]">
        {t("lightbox.nameFacesSmall", { n: small })}
      </p>
    ) : null}
    </div>
  );
}

/** With the info panel open, the faces of the photo are marked where they are (VIEW-026). */
function PhotoFaces({ id, photo }: { id: string; photo: { w: number; h: number } }) {
  const { i18n } = useTranslation();
  const info = useQuery({ queryKey: ["asset", id, i18n.language], queryFn: () => api.asset(id, i18n.language) });
  const people = (info.data?.people ?? []).flatMap((p) => {
    const f = info.data?.people_faces?.[p.id];
    return f ? [{ id: p.id, name: p.name, bbox: f.bbox }] : [];
  });
  const unknown = info.data?.unknown_faces ?? [];
  return people.length || unknown.length ? <FaceBoxes photo={photo} people={people} unknown={unknown} /> : null;
}

const SLIDE_MS = 4000;

/** The Lightbox's keys, from 「?」 or its button (VIEW-040). Esc closes the list, not the Lightbox. */
function KeyList({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => ref.current?.focus(), []);
  const rows: Array<[string, string]> = [
    ["← →", t("lightbox.k.prevNext")],
    [t("lightbox.k.space"), t("lightbox.k.play")],
    ["F", t("lightbox.k.fav")],
    ["R", t("lightbox.k.rotate")],
    ["I", t("lightbox.k.info")],
    ["+ − 0", t("lightbox.k.zoom")],
    ["?", t("lightbox.keys")],
    ["Esc", t("lightbox.k.close")],
  ];
  return (
    <div
      ref={ref}
      role="dialog"
      aria-label={t("lightbox.keys")}
      tabIndex={-1}
      className="key-list"
      onKeyDown={(e) => {
        e.stopPropagation(); // keys here are not the Lightbox's
        if (e.key === "Escape" || e.key === "?") onClose();
      }}
    >
      <div className="flex items-center">
        <h2 className="font-semibold">{t("lightbox.keys")}</h2>
        <button type="button" className="ml-auto underline text-sm" onClick={onClose}>
          {t("lightbox.close")}
        </button>
      </div>
      <dl>
        {rows.map(([k, what]) => (
          <div key={k} className="flex gap-3 py-1">
            <dt className="w-20 shrink-0">
              <kbd>{k}</kbd>
            </dt>
            <dd>{what}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

