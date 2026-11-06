// The card that stays in the corner on every page while photos are being added or organized (ADR-0029). Users could
// not tell whether anything was happening when progress lived only behind the Activity button: the most common
// complaint about the photo apps we studied, too. It answers four questions: how far along, which folders, which
// photo right now, and what the AI just found.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, mediaUrl, type AnalysisStatus } from "../../lib/api";
import { etaText } from "../../lib/format";
import { useActivity } from "./ActivityHost";
import type { Op } from "./model";

const STAGES = ["scan", "job.label", "output.apply", "output.undo"];
const LIVE = ["running", "queued", "waiting", "paused", "pausing"];
const REFRESH_MS = 5000;
const DETAIL_MS = 1500;
const DONE_MARK = "✓ ";

type Progress = { done?: number; total?: number };
const progressOf = (o: Op) => (o.progress ?? {}) as Progress;
type Stage = { type: string; done: number; total: number; eta: Op["eta"] };

/** Several folders can be in the same stage: one bar for the stage, the folders listed underneath. */
function stagesOf(ops: Op[]): Stage[] {
  return STAGES.flatMap((type) => {
    const list = ops.filter((o) => o.type === type);
    if (!list.length) return [];
    const sum = (f: (p: Progress) => number | undefined) => list.reduce((n, o) => n + (f(progressOf(o)) ?? 0), 0);
    return [{ type, done: sum((p) => p.done), total: sum((p) => p.total), eta: list.find((o) => o.status === "running" && o.eta)?.eta ?? null }];
  });
}

export function ProgressDock() {
  const { t, i18n } = useTranslation();
  const qc = useQueryClient();
  const ops = (useActivity().data ?? []).filter((o) => STAGES.includes(o.type) && LIVE.includes(o.status));
  const stages = stagesOf(ops);
  const [small, setSmall] = useState(false);
  const [finished, setFinished] = useState<number | null>(null);
  const analysed = useRef<number | null>(null);
  const busy = ops.length > 0;
  const analysing = stages.some((s) => s.type === "job.label");
  const detail = useQuery({
    queryKey: ["analysis-status", i18n.language],
    queryFn: () => api.analysisStatus(i18n.language),
    enabled: analysing && !small,
    refetchInterval: DETAIL_MS,
  });

  const label = stages.find((s) => s.type === "job.label");
  if (label) analysed.current = Math.max(label.total, analysed.current ?? 0);
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
  const pctOf = (s: Stage) => (s.total ? Math.round((100 * s.done) / s.total) : 0);
  const lead = stages.find((s) => s.total) ?? stages[0];
  if (small) {
    return (
      <aside data-testid="progress-dock" aria-label={t("dock.label")} className="progress-dock progress-dock-small">
        <button type="button" onClick={() => setSmall(false)} className="flex w-full justify-between gap-3">
          <span>{t(`dock.stage.${lead.type}`)}</span>
          <span className="tnum">{t("common.percent", { n: pctOf(lead) })}</span>
        </button>
      </aside>
    );
  }
  const num = (n: number) => n.toLocaleString(i18n.language);
  return (
    <aside data-testid="progress-dock" aria-label={t("dock.label")} className="progress-dock">
      <div className="flex items-center justify-between">
        <p className="font-semibold">{t("dock.title")}</p>
        <button type="button" className="text-sm underline" onClick={() => setSmall(true)}>{t("dock.minimize")}</button>
      </div>
      {stages.map((s) => {
        const eta = s.eta ? etaText(s.eta, i18n.language) : null;
        return (
          <div key={s.type} className="space-y-1">
            <div className="flex justify-between gap-3 text-sm">
              <span>{t(`dock.stage.${s.type}`)}</span>
              <span className="tnum text-[var(--text-muted)]">
                {s.total ? `${num(s.done)} / ${num(s.total)}` : t(stages.some((x) => x.type === "scan") ? "dock.waitingScan" : "dock.preparing")}
              </span>
            </div>
            <div role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pctOf(s)} aria-label={t(`dock.stage.${s.type}`)} className="h-1.5 w-full rounded bg-[var(--surface-2)]">
              <div className="h-full rounded bg-[var(--accent)]" style={{ width: `${pctOf(s)}%` }} />
            </div>
            {eta ? <p className="tnum text-xs text-[var(--text-muted)]">{eta}</p> : null}
          </div>
        );
      })}
      {detail.data ? <Detail status={detail.data} /> : null}
      <p className="text-xs text-[var(--text-muted)]">{t("dock.keepUsing")}</p>
    </aside>
  );
}

function Detail({ status }: { status: AnalysisStatus }) {
  const { t, i18n } = useTranslation();
  const num = (n: number) => n.toLocaleString(i18n.language);
  const now = status.current[0];
  const last = status.recent[0];
  const base = (rel: string) => rel.slice(rel.lastIndexOf("/") + 1);
  const state = (j: AnalysisStatus["jobs"][number]) => (j.state === "analysing" ? `${num(j.done)} / ${num(j.total)}` : t(`dock.state.${j.state}`));
  return (
    <>
      {now ? (
        <div className="dock-photo">
          <img src={mediaUrl(now.asset_id, "t256", now.ch)} alt="" />
          <div className="min-w-0">
            <p className="text-xs text-[var(--text-muted)]">{t("dock.now")}</p>
            <p className="truncate text-sm">{base(now.rel)}</p>
          </div>
        </div>
      ) : null}
      {last ? (
        <div className="dock-photo">
          <img src={mediaUrl(last.asset_id, "t256", last.ch)} alt="" />
          <div className="min-w-0">
            <p className="text-xs text-[var(--text-muted)]">
              {t("dock.latest")}
              {last.category_name ? ` · ${last.category_name}` : ""}
            </p>
            <p className="dock-caption text-sm">{last.caption ?? base(last.rel)}</p>
          </div>
        </div>
      ) : null}
      {status.jobs.length > 0 ? (
        <ul aria-label={t("dock.folders")} className="dock-folders">
          {status.jobs.map((j) => (
            <li key={j.job_id}>
              <span className="truncate">{j.folder}</span>
              <span className="tnum flex-none text-[var(--text-muted)]">
                {state(j)}
                {j.failed ? ` · ${t("dock.failed", { n: num(j.failed) })}` : ""}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </>
  );
}
