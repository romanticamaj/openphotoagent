// Lightbox (10 §3): progressive ThumbHash → t512 → p1024, neighbours preloaded, zoom/pan, filmstrip, `I` info
// panel with EXIF and every AI label's model and decision layer, inline correction (→ review), reveal in file
// manager on desktop only. role="dialog" aria-modal; position is announced through aria-live.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { FaceCrop } from "../../components/FaceCrop";
import { FaceBoxes } from "./faceBoxes";
import { Suspense, lazy, useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, mediaUrl, type AssetInfo, type TimelineAsset } from "../../lib/api";
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
  const [film, setFilm] = useState<Array<{ ord: number; a: TimelineAsset }>>([]);
  const [stage, setStage] = useState<"t512" | "p1024" | null>(null);
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
    setScale(1);
    setPan({ x: 0, y: 0 });
    void grid.at(ord).then((r) => live && setAsset(r?.asset ?? null));
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

  const close = useCallback(() => onClose(asset?.id ?? null), [asset, onClose]);
  const go = useCallback((d: number) => setOrd((o) => Math.max(0, Math.min(grid.total - 1, o + d))), [grid.total]);

  const onKey = (e: React.KeyboardEvent) => {
    if ((e.target as HTMLElement).tagName === "SELECT") return;
    const k: Record<string, () => void> = {
      Escape: close,
      ArrowLeft: () => go(-1),
      ArrowRight: () => go(1),
      i: () => setShowInfo((s) => !s),
      I: () => setShowInfo((s) => !s),
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
      <div className="col-span-1 flex items-center gap-2 px-3">
        <button type="button" onClick={close} className="rounded-[var(--radius-sm)] px-2 py-1 hover:bg-[var(--surface-2)]">{t("lightbox.close")}</button>
        <span aria-live="polite" className="tnum text-[var(--text)]" data-testid="lightbox-position">{position}</span>
        <span className="flex-1" />
        <button type="button" onClick={() => setScale((s) => Math.max(1, s / 1.5))} aria-label={t("lightbox.zoomOut")} className="px-2">−</button>
        <button type="button" onClick={() => setScale((s) => Math.min(8, s * 1.5))} aria-label={t("lightbox.zoomIn")} className="px-2">+</button>
        <button type="button" aria-pressed={showInfo} onClick={() => setShowInfo((s) => !s)} className="rounded-[var(--radius-sm)] px-2 py-1 hover:bg-[var(--surface-2)]">{t("lightbox.info")}</button>
      </div>
      <div
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
              style={{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${scale})`, width: "100%", height: "100%" }}
            />
            {stage !== null && (
              <img
                key={`${asset.id}-p1024`}
                src={mediaUrl(asset.id, "p1024", asset.ch)}
                alt={asset.taken_at ? t("viewer.photoAlt", { date: asset.taken_at.slice(0, 10) }) : ""}
                data-variant="p1024"
                onLoad={() => setStage("p1024")}
                style={{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${scale})`, width: "100%", height: "100%" }}
              />
            )}
          </>
        )}
        {asset && showInfo && natural && scale === 1 ? <PhotoFaces id={asset.id} photo={natural} /> : null}
        {grid.total > 1 ? (
          <>
            <button type="button" onClick={() => go(-1)} aria-label={t("lightbox.prev")} className="absolute left-2 rounded-full bg-black/40 px-3 py-2">‹</button>
            <button type="button" onClick={() => go(1)} aria-label={t("lightbox.next")} className="absolute right-2 rounded-full bg-black/40 px-3 py-2">›</button>
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
            style={{ backgroundImage: `url(${mediaUrl(f.a.id, "t256", f.a.ch)}), url(${placeholder(f.a.thumbhash) ?? ""})` }}
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
  const f = [...root.querySelectorAll<HTMLElement>("button, select, input, [tabindex='0']")].filter((x) => !x.hasAttribute("disabled"));
  if (!f.length) return;
  const first = f[0];
  const last = f[f.length - 1];
  if (e.shiftKey && document.activeElement === first) {
    e.preventDefault();
    last.focus();
  } else if (!e.shiftKey && document.activeElement === last) {
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
    [t("lightbox.exif.size"), a.width && a.height ? `${a.width} × ${a.height}` : null],
  ];
  return (
    <aside className="info space-y-4" aria-label={t("lightbox.info")} data-testid="info-panel">
      <section aria-labelledby="exif-h">
        <h2 id="exif-h" className="mb-2 font-semibold">{t("lightbox.exif.title")}</h2>
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
          {rows
            .filter(([, v]) => v)
            .map(([k, v]) => (
              <div key={k} className="contents">
                <dt className="text-[var(--text-muted)]">{k}</dt>
                <dd className="tnum">{v}</dd>
              </div>
            ))}
        </dl>
      </section>
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
          <p data-testid="faces-nobody" className="mb-3 text-sm text-[var(--text-muted)]">
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
          </p>
        ) : null}
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
        <form
          className="mt-3 flex items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (pick) save.mutate();
          }}
        >
          <label className="flex flex-col gap-1 text-sm">
            <span>{t("lightbox.correct")}</span>
            <select value={pick} onChange={(e) => setPick(e.target.value)} className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--bg)] px-2 py-1">
              <option value="">—</option>
              {(preset.data?.categories ?? []).map((c) => (
                <option key={c.key} value={c.key}>
                  {c.name}
                </option>
              ))}
            </select>
          </label>
          {pick ? (
            <button type="submit" className="rounded-[var(--radius-md)] bg-[var(--accent)] px-3 py-1 text-[var(--accent-fg)]">{t("lightbox.correctSave")}</button>
          ) : null}
        </form>
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
                  <button type="button" onClick={() => void p.revealInFileManager!(l.root_alias, l.rel_path)} className="shrink-0 text-[var(--accent)] underline">
                    {t("lightbox.reveal")}
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
