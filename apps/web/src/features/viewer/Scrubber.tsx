// Date scrubber (10 §2.3): months take space proportional to their photo count, so dragging maps straight to a
// bucket without loading anything. role="slider" with "2025 年 7 月，312 張" as aria-valuetext. `G` opens
// "jump to date".
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { monthLabel, type TimelineModel } from "./timeline";

export function parseMonth(s: string): string | null {
  const t = s.trim();
  let m = /^(\d{4})\s*[-/.年]\s*(\d{1,2})\s*月?$/.exec(t);
  if (m && +m[2] >= 1 && +m[2] <= 12) return `${m[1]}-${m[2].padStart(2, "0")}`;
  m = /^民國\s*(\d{2,3})\s*年(?:\s*(\d{1,2})\s*月)?$/.exec(t);
  if (m) return `${+m[1] + 1911}-${(m[2] ?? "1").padStart(2, "0")}`;
  m = /^(\d{4})\s*年?$/.exec(t);
  if (m) return `${m[1]}-12`;
  return null;
}

export function Scrubber({ model, current, onJump }: { model: TimelineModel; current: number; onJump: (i: number) => void }) {
  const { t, i18n } = useTranslation();
  const ref = useRef<HTMLDivElement>(null);
  const [drag, setDrag] = useState<number | null>(null);
  const [jumpOpen, setJumpOpen] = useState(false);
  const shown = drag ?? current;
  const b = model.buckets[shown];
  const label = b ? (monthLabel(b.month, i18n.language) ?? t("viewer.unknownDate")) : "";
  const valuetext = b ? t("viewer.scrubberValue", { month: label, count: b.count }) : "";

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (e.key.toLowerCase() === "g" && !e.ctrlKey && !e.metaKey && !e.altKey && tag !== "INPUT" && tag !== "TEXTAREA") {
        if (document.querySelector('[role="dialog"][aria-modal="true"]')) return;
        e.preventDefault();
        setJumpOpen(true);
      }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);

  const indexAtY = (clientY: number) => {
    const r = ref.current!.getBoundingClientRect();
    return model.indexAtScrub((clientY - r.top) / Math.max(1, r.height));
  };

  const years: Array<{ year: string; f: number }> = [];
  model.buckets.forEach((bk, i) => {
    const y = bk.month.slice(0, 4);
    if (/^\d{4}$/.test(y) && years[years.length - 1]?.year !== y) years.push({ year: y, f: model.scrubFraction(i) });
  });
  // thin out year labels when they would overlap (≥ 4 % apart)
  const yearLabels = years.filter((y, i) => i === 0 || y.f - years[i - 1].f > 0.04);

  return (
    <>
      <div
        ref={ref}
        className="scrubber"
        role="slider"
        tabIndex={0}
        aria-label={t("viewer.scrubber")}
        aria-orientation="vertical"
        aria-valuemin={0}
        aria-valuemax={Math.max(0, model.buckets.length - 1)}
        aria-valuenow={shown}
        aria-valuetext={valuetext}
        onPointerDown={(e) => {
          e.currentTarget.setPointerCapture(e.pointerId);
          const i = indexAtY(e.clientY);
          setDrag(i);
          onJump(i);
        }}
        onPointerMove={(e) => {
          if (drag === null) return;
          const i = indexAtY(e.clientY);
          if (i !== drag) {
            setDrag(i);
            onJump(i);
          }
        }}
        onPointerUp={() => setDrag(null)}
        onPointerCancel={() => setDrag(null)}
        onKeyDown={(e) => {
          const n = model.buckets.length - 1;
          const next = { ArrowDown: shown + 1, ArrowUp: shown - 1, PageDown: shown + 6, PageUp: shown - 6, Home: 0, End: n }[e.key];
          if (next !== undefined) {
            e.preventDefault();
            onJump(Math.max(0, Math.min(n, next)));
          }
        }}
      >
        {yearLabels.map((y) => (
          <span key={y.year} aria-hidden="true" className="tnum absolute right-2 text-xs text-[var(--text-muted)]" style={{ top: `${y.f * 100}%` }}>
            {y.year}
          </span>
        ))}
        <span className="thumb" aria-hidden="true" style={{ top: `${model.scrubFraction(shown) * 100}%` }} />
        {drag !== null && (
          <span className="bubble" aria-hidden="true" style={{ top: `${model.scrubFraction(shown) * 100}%` }}>
            {label}
          </span>
        )}
      </div>
      {jumpOpen && (
        <JumpDialog
          onClose={() => setJumpOpen(false)}
          onGo={(month) => {
            const i = model.buckets.findIndex((bk) => bk.month <= month && /^\d/.test(bk.month));
            onJump(i < 0 ? model.buckets.length - 1 : i);
            setJumpOpen(false);
          }}
        />
      )}
    </>
  );
}

function JumpDialog({ onClose, onGo }: { onClose: () => void; onGo: (month: string) => void }) {
  const { t } = useTranslation();
  const [v, setV] = useState("");
  const [err, setErr] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => input.current?.focus(), []);
  return (
    <div role="dialog" aria-modal="true" aria-labelledby="jump-title" className="fixed inset-0 z-40 grid place-items-center bg-black/50" onKeyDown={(e) => e.key === "Escape" && onClose()}>
      <form
        className="space-y-3 rounded-[var(--radius-lg)] bg-[var(--surface)] p-6"
        onSubmit={(e) => {
          e.preventDefault();
          const m = parseMonth(v);
          if (m) onGo(m);
          else setErr(true);
        }}
      >
        <h2 id="jump-title" className="text-base font-semibold">{t("viewer.jump")}</h2>
        <label className="block space-y-1">
          <span className="text-sm text-[var(--text-muted)]">{t("viewer.jumpLabel")}</span>
          <input
            ref={input}
            value={v}
            onChange={(e) => {
              setV(e.target.value);
              setErr(false);
            }}
            aria-invalid={err}
            aria-describedby={err ? "jump-err" : undefined}
            className="block w-64 rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--bg)] px-2 py-1"
          />
        </label>
        {err && <p id="jump-err" role="alert" className="text-sm text-[var(--danger)]">{t("viewer.jumpInvalid")}</p>}
        <div className="flex gap-2">
          <button type="submit" className="rounded-[var(--radius-md)] bg-[var(--accent)] px-4 py-1.5 text-[var(--accent-fg)]">{t("viewer.jumpGo")}</button>
          <button type="button" onClick={onClose} className="rounded-[var(--radius-md)] border border-[var(--border)] px-4 py-1.5">{t("lightbox.close")}</button>
        </div>
      </form>
    </div>
  );
}
