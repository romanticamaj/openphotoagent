// The editor (ADR-0057): light, colour, a filter, an aspect crop and straighten, shown at once on the photo and kept
// in the library on 完成; the file is never written. Free crop handles and 自動 come in a later slice.
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "../../components/Button";
import { FILTERS, type Edit, type FilterName } from "../../lib/editMath";

type Slider = { key: "exposure" | "contrast" | "highlights" | "shadows" | "saturation" | "warmth" | "straighten"; min: number; max: number; step: number; unit: string };

const SLIDERS: Slider[] = [
  { key: "exposure", min: -2, max: 2, step: 0.1, unit: " EV" },
  { key: "contrast", min: -100, max: 100, step: 1, unit: "" },
  { key: "highlights", min: -100, max: 100, step: 1, unit: "" },
  { key: "shadows", min: -100, max: 100, step: 1, unit: "" },
  { key: "saturation", min: -100, max: 100, step: 1, unit: "" },
  { key: "warmth", min: -100, max: 100, step: 1, unit: "" },
  { key: "straighten", min: -45, max: 45, step: 0.5, unit: "°" },
];

const ASPECTS: Array<{ label: string; ratio: number | null }> = [
  { label: "original", ratio: null },
  { label: "1:1", ratio: 1 },
  { label: "4:3", ratio: 4 / 3 },
  { label: "3:2", ratio: 3 / 2 },
  { label: "16:9", ratio: 16 / 9 },
];

/** The largest centred crop of this aspect in a w×h photo (after its quarter turn); none for the original. */
export function aspectCrop(ratio: number | null, w: number, h: number): Edit["crop"] {
  if (!ratio || !w || !h) return undefined;
  const r = w / h;
  if (Math.abs(r - ratio) < 1e-6) return undefined;
  if (r > ratio) {
    const cw = ratio / r;
    return { x: (1 - cw) / 2, y: 0, w: cw, h: 1 };
  }
  const ch = r / ratio;
  return { x: 0, y: (1 - ch) / 2, w: 1, h: ch };
}

const fmt = (v: number, unit: string) => `${v > 0 ? "+" : ""}${Math.round(v * 10) / 10}${unit}`;

function clean(e: Edit): Edit {
  const out: Edit = { v: 1 };
  for (const s of SLIDERS) if (e[s.key]) out[s.key] = e[s.key];
  if (e.crop) out.crop = e.crop;
  if (e.filter && e.filter.strength > 0) out.filter = e.filter;
  return out;
}

export function EditPanel({
  initial,
  w,
  h,
  onChange,
  onSave,
  onRevert,
  onCancel,
  auto,
}: {
  initial: Edit | null | undefined;
  /** the photo's size after its quarter turn, for aspect crops */
  w: number;
  h: number;
  onChange: (e: Edit) => void;
  onSave: (e: Edit) => void;
  onRevert: () => void;
  onCancel: () => void;
  /** 自動: light from the preview's brightness (EDIT-016/017) */
  auto?: () => Promise<Edit>;
}) {
  const { t } = useTranslation();
  const [e, setE] = useState<Edit>(initial ?? { v: 1 });
  // the saved crop's aspect is the one chosen: it showed 原始比例 (round 145, EDIT-022)
  const [aspect, setAspect] = useState<string>(() => {
    const c = initial?.crop;
    if (!c) return "original";
    const hit = ASPECTS.find((a) => {
      const k = aspectCrop(a.ratio, w, h);
      return k && Math.abs(k.w - c.w) < 1e-3 && Math.abs(k.h - c.h) < 1e-3;
    });
    return hit?.label ?? "free";
  });
  const first = useRef<HTMLInputElement>(null);
  useEffect(() => first.current?.focus(), []); // 31 Tabs to the first slider (round 145)
  const set = (next: Edit) => {
    const c = clean(next);
    setE(c);
    onChange(c);
  };
  return (
    <aside
      className="edit-panel"
      aria-label={t("edit.title")}
      onKeyDown={(ev) => {
        if (ev.key === "Escape") {
          ev.stopPropagation(); // the editor, not the Lightbox (Esc closed the photo)
          onCancel();
        }
      }}
    >
      <h2 className="text-sm font-semibold">{t("edit.title")}</h2>
      <section className="space-y-2" aria-label={t("edit.adjust")}>
        {SLIDERS.map((s) => {
          const v = e[s.key] ?? 0;
          return (
            <label key={s.key} className="edit-slider">
              <span>{t(`edit.${s.key}`)}</span>
              <input
                ref={s.key === "exposure" ? first : undefined}
                type="range"
                min={s.min}
                max={s.max}
                step={s.step}
                value={v}
                aria-label={t(`edit.${s.key}`)}
                aria-valuetext={fmt(v, s.unit)}
                onChange={(x) => set({ ...e, [s.key]: Number(x.target.value) })}
                onDoubleClick={() => set({ ...e, [s.key]: 0 })}
              />
              <span className="tnum text-xs text-[var(--text-muted)]">{fmt(v, s.unit)}</span>
            </label>
          );
        })}
      </section>
      <section aria-label={t("edit.crop")}>
        <p className="text-xs text-[var(--text-muted)]">{t("edit.crop")}</p>
        <div role="radiogroup" aria-label={t("edit.aspect")} className="flex flex-wrap gap-1">
          {ASPECTS.map((a) => (
            <button
              key={a.label}
              type="button"
              role="radio"
              aria-checked={aspect === a.label}
              className="chip"
              onClick={() => {
                setAspect(a.label);
                const next = { ...e };
                const crop = aspectCrop(a.ratio, w, h);
                if (crop) next.crop = crop;
                else delete next.crop;
                set(next);
              }}
            >
              {a.label === "original" ? t("edit.original") : a.label}
            </button>
          ))}
        </div>
      </section>
      <section aria-label={t("edit.filters")}>
        <p className="text-xs text-[var(--text-muted)]">{t("edit.filters")}</p>
        <div className="flex flex-wrap gap-1">
          {FILTERS.map((name: FilterName) => (
            <button
              key={name}
              type="button"
              aria-pressed={e.filter?.name === name}
              className="chip"
              onClick={() => set({ ...e, filter: e.filter?.name === name ? undefined : { name, strength: 100 } })}
            >
              {t(`edit.filter.${name}`)}
            </button>
          ))}
        </div>
      </section>
      <div className="flex flex-wrap gap-2">
        {auto ? (
          <Button
            variant="ghost"
            onClick={async () => {
              const a = await auto();
              // the light only: the crop and filter chosen stay (EDIT-017)
              set({ ...e, exposure: a.exposure, contrast: a.contrast, highlights: a.highlights, shadows: a.shadows });
            }}
          >
            {t("edit.auto")}
          </Button>
        ) : null}
        <Button onClick={() => onSave(clean(e))}>{t("edit.done")}</Button>
        <Button variant="ghost" onClick={onCancel}>
          {t("edit.cancel")}
        </Button>
        <Button variant="ghost" onClick={onRevert}>
          {t("edit.revert")}
        </Button>
      </div>
    </aside>
  );
}
