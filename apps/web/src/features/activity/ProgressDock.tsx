// The card that stays in the corner on every page while photos are being added or organized (ADR-0029). Users could
// not tell whether anything was happening when progress lived only behind the Activity button: the most common
// complaint about the photo apps we studied, too.
import { useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { etaText } from "../../lib/format";
import { useActivity } from "./ActivityHost";
import type { Op } from "./model";

const STAGES = ["scan", "job.label", "output.apply", "output.undo"];
const LIVE = ["running", "queued", "waiting", "paused", "pausing"];
const REFRESH_MS = 5000;
const DONE_MARK = "✓ ";

type Progress = { done?: number; total?: number };
const progressOf = (o: Op) => (o.progress ?? {}) as Progress;

export function ProgressDock() {
  const { t, i18n } = useTranslation();
  const qc = useQueryClient();
  const ops = (useActivity().data ?? []).filter((o) => STAGES.includes(o.type) && LIVE.includes(o.status));
  ops.sort((a, b) => STAGES.indexOf(a.type) - STAGES.indexOf(b.type));
  const [small, setSmall] = useState(false);
  const [finished, setFinished] = useState<number | null>(null);
  const analysed = useRef<number | null>(null);
  const busy = ops.length > 0;

  const label = ops.find((o) => o.type === "job.label");
  if (label) analysed.current = progressOf(label).total ?? analysed.current ?? 0;
  useEffect(() => {
    if (busy) {
      setFinished(null);
      // photos appear while the scan runs (IMP-006)
      const h = setInterval(() => void qc.invalidateQueries({ queryKey: ["timeline"] }), REFRESH_MS);
      return () => clearInterval(h);
    }
    if (analysed.current !== null) {
      setFinished(analysed.current);
      analysed.current = null;
      void qc.invalidateQueries({ queryKey: ["timeline"] });
    }
    return undefined;
  }, [busy, qc]);

  if (finished !== null) {
    return (
      <aside data-testid="progress-dock" aria-label={t("dock.label")} className="progress-dock">
        <p className="font-semibold">
          <span aria-hidden="true" style={{ color: "var(--success)" }}>{DONE_MARK}</span>
          {finished ? t("dock.doneCount", { n: finished.toLocaleString(i18n.language) }) : t("dock.done")}
        </p>
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <Link to="/" className="underline">{t("dock.seePhotos")}</Link>
          <Link to="/organize" className="underline">{t("dock.organize")}</Link>
          <button type="button" className="ml-auto underline" onClick={() => setFinished(null)}>{t("dock.close")}</button>
        </div>
      </aside>
    );
  }
  if (!busy) return null;
  const lead = ops.find((o) => progressOf(o).total) ?? ops[0];
  const pct = Math.round(100 * (lead.fraction ?? 0));
  if (small) {
    return (
      <aside data-testid="progress-dock" aria-label={t("dock.label")} className="progress-dock progress-dock-small">
        <button type="button" onClick={() => setSmall(false)} className="flex w-full justify-between gap-3">
          <span>{t(`dock.stage.${lead.type}`)}</span>
          <span className="tnum">{t("common.percent", { n: pct })}</span>
        </button>
      </aside>
    );
  }
  return (
    <aside data-testid="progress-dock" aria-label={t("dock.label")} className="progress-dock">
      <div className="flex items-center justify-between">
        <p className="font-semibold">{t("dock.title")}</p>
        <button type="button" className="text-sm underline" onClick={() => setSmall(true)}>{t("dock.minimize")}</button>
      </div>
      {ops.map((o) => {
        const { done, total } = progressOf(o);
        const eta = o.status === "running" && o.eta ? etaText(o.eta, i18n.language) : null;
        return (
          <div key={o.id} className="space-y-1">
            <div className="flex justify-between gap-3 text-sm">
              <span>{t(`dock.stage.${o.type}`)}</span>
              <span className="tnum text-[var(--text-muted)]">
                {total ? `${(done ?? 0).toLocaleString(i18n.language)} / ${total.toLocaleString(i18n.language)}` : t("dock.waitingScan")}
              </span>
            </div>
            <div role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(100 * (o.fraction ?? 0))} aria-label={t(`dock.stage.${o.type}`)} className="h-1.5 w-full rounded bg-[var(--surface-2)]">
              <div className="h-full rounded bg-[var(--accent)]" style={{ width: `${Math.round(100 * (o.fraction ?? 0))}%` }} />
            </div>
            {eta ? <p className="tnum text-xs text-[var(--text-muted)]">{eta}</p> : null}
          </div>
        );
      })}
      <p className="text-xs text-[var(--text-muted)]">{t("dock.keepUsing")}</p>
    </aside>
  );
}
