// Organize (04 §3.2 + §8.2 single-user simplification): folder → how (by date / by content / custom categories) and
// where → for content methods a live 10-photo preview with threshold sliders, the library-wide forecast and custom
// category cards that accept dragged example photos → create the plan → the plan page (approve, apply, undo).
import { useMutation, useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button, type DisabledReason } from "../../components/Button";
import { CompatBadge } from "../../components/CompatBadge";
import { useServerEvents } from "../../lib/events";
import { FolderPicker } from "../../components/FolderPicker";
import { ProblemCard, type Problem } from "../../components/ProblemCard";
import { ApiError, api, mediaUrl, type Category, type Job, type PreviewItem } from "../../lib/api";
import { formatDuration } from "../../lib/format";

type Method = "by-date" | "by-content-12" | "custom";

export async function waitForJob(jobId: string, until: (j: Job) => boolean, onTick?: (j: Job) => void): Promise<Job> {
  for (;;) {
    const j = await api.job(jobId);
    onTick?.(j);
    if (until(j)) return j;
    if (j.status === "failed") throw new Error("job failed");
    await new Promise((r) => setTimeout(r, 250));
  }
}

export function OrganizeWizard() {
  const { t, i18n } = useTranslation();
  const nav = useNavigate();
  const [folder, setFolder] = useState<{ root: string; path: string; source: string } | null>(null);
  const [method, setMethod] = useState<Method | null>(null);
  const [allowMove, setAllowMove] = useState(false);
  const [cats, setCats] = useState<Category[]>([]);
  const [thresholds, setThresholds] = useState({ accept: 0.85, review: 0.5 });
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [running, setRunning] = useState<Job | null>(null);
  const [gate, setGate] = useState<DisabledReason | null>(null);
  const [autoStart, setAutoStart] = useState(false);
  const [announce, setAnnounce] = useState("");
  const live = (
    <p data-testid="readiness-live" aria-live="polite" className="text-sm">
      {announce}
    </p>
  );
  const roots = useQuery({ queryKey: ["roots"], queryFn: api.roots });
  const target = (roots.data ?? []).find((r) => r.mode === "rw" && r.alias !== folder?.root) ?? (roots.data ?? []).find((r) => r.mode === "rw");
  const preset = useQuery({
    queryKey: ["preset", "by-content-12", i18n.language],
    queryFn: () => api.preset("by-content-12", i18n.language),
  });
  const names = useMemo(() => {
    const m = new Map((preset.data?.categories ?? []).map((c) => [c.key, c.name]));
    for (const c of cats) m.set(c.key, c.name);
    return m;
  }, [preset.data, cats]);

  const pick = useMutation({
    mutationFn: async ({ root, path }: { root: string; path: string }) => {
      const s = await api.createSource(root, path);
      setFolder({ root, path, source: s.id });
    },
  });
  const startPreview = useMutation({
    mutationFn: async () => {
      const p = await api.createPreview(folder!.source, method!, method === "custom" ? cats : undefined);
      setPreviewId(p.id);
    },
  });
  const preview = useQuery({
    queryKey: ["preview", previewId],
    queryFn: () => api.preview(previewId!),
    enabled: Boolean(previewId),
    refetchInterval: (q) => (q.state.data?.status === "ready" && q.state.data.estimate ? false : 500),
  });

  const create = useMutation({
    mutationFn: async () => {
      const extra: Record<string, unknown> = {};
      if (allowMove) Object.assign(extra, { action: "move", advanced: { allow_move: true } });
      if (method === "custom") extra.categories = cats;
      const j = await api.createJob(folder!.source, method!, { root_alias: target!.alias, sub_path: "" }, extra);
      let ready = await waitForJob(j.id, (x) => x.status === "awaiting_approval", setRunning);
      const changed = thresholds.accept !== 0.85 || thresholds.review !== 0.5;
      if (method !== "by-date" && changed) {
        await api.reapply(j.id, thresholds);
        ready = await api.job(j.id);
      }
      if ((ready.reviews_pending ?? 0) > 0) {
        setRunning(ready);
        return;
      }
      void nav({ to: "/plans/$planId", params: { planId: ready.plan_id! } });
    },
  });

  if (!folder) {
    return <FolderPicker onPick={(root, path) => pick.mutate({ root, path })} />;
  }

  // a running job: progress, waiting reason, pending reviews
  if (running && (create.isPending || (running.reviews_pending ?? 0) > 0)) {
    return (
      <>
        {live}
        <JobProgress job={running} onSkipped={(planId) => void nav({ to: "/plans/$planId", params: { planId } })} />
      </>
    );
  }

  const methodReason = !method
    ? { code: "METHOD_REQUIRED", message: t("organize.pickMethodFirst") }
    : !target
      ? { code: "FS_NOT_WRITABLE", message: t("organize.noWritableTarget") }
      : null;
  const busy = startPreview.isPending || create.isPending ? { code: "BUSY", message: t("common.working") } : null;
  const contentGate = method && method !== "by-date" ? gate : null;

  return (
    <section className="space-y-6">
      {live}
      <fieldset className="space-y-2">
        <legend className="text-lg font-semibold">{t("organize.method")}</legend>
        {(
          [
            ["by-date", "organize.byDate", "organize.byDateHint"],
            ["by-content-12", "organize.byContent", "organize.byContentHint"],
            ["custom", "organize.custom", "organize.customHint"],
          ] as const
        ).map(([m, label, hint]) => (
          <label key={m} className="flex items-center gap-2">
            <input
              type="radio"
              name="method"
              value={m}
              checked={method === m}
              onChange={() => {
                setMethod(m);
                setPreviewId(null);
              }}
            />
            <span>{t(label)}</span>
            <span className="text-sm text-[var(--text-muted)]">{t(hint)}</span>
          </label>
        ))}
      </fieldset>
      <p>
        {t("organize.output")}: <strong data-testid="output-target">{target?.alias ?? "—"}</strong>
      </p>
      <details className="space-y-2">
        <summary className="cursor-pointer">{t("organize.advanced")}</summary>
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={allowMove} onChange={(e) => setAllowMove(e.target.checked)} />
          {t("organize.allowMove")}
        </label>
        {allowMove && (
          <p role="note" className="text-sm" style={{ color: "var(--warning)" }}>
            ⚠ {t("organize.moveWarning")}
          </p>
        )}
      </details>

      {method === "custom" && (
        <CategoryEditor
          cats={cats}
          setCats={setCats}
          hits={preview.data?.estimate?.distribution}
          onExamplesChanged={() => startPreview.mutate()}
        />
      )}

      {previewId && method !== "by-date" && (
        <PreviewPanel
          previewId={previewId}
          items={preview.data?.items ?? []}
          estimate={preview.data?.estimate ?? null}
          names={names}
          thresholds={thresholds}
          setThresholds={setThresholds}
          lang={i18n.language}
        />
      )}

      {method && method !== "by-date" && folder && target && (
        <Readiness
          params={{ kind: "organize", preset: method, source_id: folder.source, target: { root_alias: target.alias, sub_path: "" } }}
          onGate={setGate}
          autoStart={autoStart}
          setAutoStart={setAutoStart}
          onReady={() => {
            setAnnounce(t("readiness.ready"));
            if (autoStart && !create.isPending) create.mutate();
          }}
        />
      )}

      <div className="flex gap-3">
        {method === "by-date" ? (
          <Button disabledReason={methodReason ?? busy} onClick={() => create.mutate()}>
            {t("organize.preview")}
          </Button>
        ) : (
          <>
            <Button disabledReason={methodReason ?? contentGate ?? busy} onClick={() => startPreview.mutate()}>
              {t("organize.preview")}
            </Button>
            <Button variant="ghost" disabledReason={methodReason ?? contentGate ?? busy} onClick={() => create.mutate()}>
              {t("organize.createPlan")}
            </Button>
          </>
        )}
      </div>
      {(create.error || startPreview.error) && <Blockers error={create.error ?? startPreview.error} />}


    </section>
  );
}

/** ReadinessChecklist (06 §6.3): the models this job would use with their CompatBadge, the first blocker as the
 * buttons' reason, and automatic re-check when a download finishes (PF-077). */
function Readiness({
  params,
  onGate,
  autoStart,
  setAutoStart,
  onReady,
}: {
  params: Record<string, unknown>;
  onGate: (g: DisabledReason | null) => void;
  autoStart: boolean;
  setAutoStart: (v: boolean) => void;
  onReady: () => void;
}) {
  const { t } = useTranslation();
  const pf = useQuery({
    queryKey: ["preflight", "job.start", params],
    queryFn: () => api.preflightFull("job.start", params),
    // SSE is the trigger; a slow poll covers a dropped connection
    refetchInterval: (q) => (q.state.data && !q.state.data.ready ? 5000 : false),
  });
  useServerEvents(["model.installed", "doctor.changed"], () => void pf.refetch());
  const wasBlocked = useRef(false);
  const blocker = pf.data?.blockers[0];
  useEffect(() => {
    if (!pf.data) return;
    if (blocker) {
      wasBlocked.current = true;
      onGate({ code: blocker.code, message: t(`errors.${blocker.code}.message`, { ...blocker.params, defaultValue: blocker.code }) });
    } else {
      onGate(null);
      if (wasBlocked.current) {
        wasBlocked.current = false;
        onReady();
      }
    }
  }, [pf.data]);
  return <ReadinessChecklist models={pf.data?.models ?? []} blocked={Boolean(blocker)} autoStart={autoStart} setAutoStart={setAutoStart} />;
}

/** Presentational ReadinessChecklist (06 §6.3): the models with their CompatBadge and the auto-start option. */
export function ReadinessChecklist({
  models,
  blocked,
  autoStart,
  setAutoStart,
}: {
  models: Array<{ key: string; compat: { level: string; reasons: Array<{ code: string; params?: Record<string, unknown> }> } }>;
  blocked: boolean;
  autoStart: boolean;
  setAutoStart: (v: boolean) => void;
}) {
  const { t } = useTranslation();
  return (
    <section data-testid="readiness" aria-label={t("readiness.title")} className="space-y-2 rounded-[var(--radius-md)] border border-[var(--border)] p-3">
      <h2 className="text-sm font-semibold">{t("readiness.title")}</h2>
      <ul>
        {models.map((m) => (
          <li key={m.key} data-testid={`model-${m.key}`} className="flex items-center gap-3 text-sm">
            <span>
              {t("readiness.model")}: {m.key}
            </span>
            <CompatBadge compat={m.compat} />
          </li>
        ))}
      </ul>
      {blocked && (
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={autoStart} onChange={(e) => setAutoStart(e.target.checked)} />
          {t("readiness.autoStart")}
        </label>
      )}
    </section>
  );
}

function PreviewPanel({
  previewId,
  items,
  estimate,
  names,
  thresholds,
  setThresholds,
  lang,
}: {
  previewId: string;
  items: PreviewItem[];
  estimate: { total: number; distribution: Record<string, number>; time: { p50_s: number } } | null;
  names: Map<string, string>;
  thresholds: { accept: number; review: number };
  setThresholds: (t: { accept: number; review: number }) => void;
  lang: string;
}) {
  const { t } = useTranslation();
  // thresholds never re-run inference (invariant 7): the server re-derives decisions from the stored stages
  const [shown, setShown] = useState<PreviewItem[]>(items);
  useEffect(() => setShown(items), [items]);
  const seq = useRef(0);
  const change = (next: { accept: number; review: number }) => {
    setThresholds(next);
    const n = ++seq.current;
    void api.rerender(previewId, next).then((r) => n === seq.current && setShown(r.items));
  };
  return (
    <section aria-labelledby="samples-h" className="space-y-4">
      <h2 id="samples-h" className="text-lg font-semibold">{t("organize.samples")}</h2>
      <div className="grid grid-cols-5 gap-2">
        {shown.map((it) => (
          <figure
            key={it.asset_id}
            data-testid="sample-card"
            data-decision={it.decision}
            draggable
            onDragStart={(e) => e.dataTransfer.setData("text/asset-id", it.asset_id)}
            className="space-y-1 rounded-[var(--radius-md)] bg-[var(--surface)] p-1"
          >
            <img src={mediaUrl(it.asset_id, "t256")} alt="" className="aspect-square w-full rounded-[var(--radius-photo)] object-cover" />
            <figcaption className="text-xs">
              <span>{it.decision === "other" ? names.get("other") ?? "other" : (names.get(it.category_key) ?? it.category_key)}</span>{" "}
              <span className="tnum text-[var(--text-muted)]">{t("common.percent", { n: Math.round((it.confidence ?? 0) * 100) })}</span>{" "}
              <span>{t(`organize.decision.${it.decision}`, { defaultValue: it.decision })}</span>
            </figcaption>
          </figure>
        ))}
      </div>
      <div className="grid max-w-xl grid-cols-[auto_1fr_auto] items-center gap-3">
        {(["accept", "review"] as const).map((k) => (
          <ThresholdRow
            key={k}
            label={t(k === "accept" ? "organize.acceptThreshold" : "organize.reviewThreshold")}
            value={thresholds[k]}
            onChange={(v) => change({ ...thresholds, [k]: v })}
          />
        ))}
      </div>
      <section aria-labelledby="dist-h">
        <h3 id="dist-h" className="font-semibold">{t("organize.distribution")}</h3>
        {estimate ? (
          <>
            <ul data-testid="library-distribution" className="grid grid-cols-3 gap-1 text-sm">
              {Object.entries(estimate.distribution)
                .sort((a, b) => b[1] - a[1])
                .map(([k, n]) => (
                  <li key={k} className="tnum">
                    {names.get(k) ?? k} {t("search.count", { n: n.toLocaleString(lang) })}
                  </li>
                ))}
            </ul>
            <p data-testid="library-time" className="tnum">
              {t("organize.libraryTime", { time: formatDuration(estimate.time.p50_s * 1000, lang) })}
            </p>
          </>
        ) : (
          <p role="status">{t("organize.estimating")}</p>
        )}
      </section>
    </section>
  );
}

function ThresholdRow({ label, value, onChange }: { label: string; value: number; onChange: (v: number) => void }) {
  return (
    <>
      <span>{label}</span>
      <input type="range" min={0} max={1} step={0.01} value={value} aria-label={label} onChange={(e) => onChange(Number(e.target.value))} />
      <span className="tnum">{value.toFixed(2)}</span>
    </>
  );
}

function CategoryEditor({
  cats,
  setCats,
  hits,
  onExamplesChanged,
}: {
  cats: Category[];
  setCats: (c: Category[]) => void;
  hits?: Record<string, number>;
  onExamplesChanged: () => void;
}) {
  const { t, i18n } = useTranslation();
  const changed = useRef(false);
  useEffect(() => {
    if (!changed.current) return;
    changed.current = false;
    const h = setTimeout(onExamplesChanged, 400); // debounce several drops into one re-preview
    return () => clearTimeout(h);
  }, [cats, onExamplesChanged]);
  const update = (i: number, patch: Partial<Category>) => setCats(cats.map((c, j) => (j === i ? { ...c, ...patch } : c)));
  return (
    <section aria-label={t("organize.custom")} className="space-y-3">
      {cats.map((c, i) => (
        <article
          key={c.key}
          data-testid="category-card"
          className="space-y-2 rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--surface)] p-3"
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            e.preventDefault();
            const id = e.dataTransfer.getData("text/asset-id");
            if (id && !(c.examples ?? []).includes(id)) {
              changed.current = true;
              update(i, { examples: [...(c.examples ?? []), id] });
            }
          }}
        >
          <span className="hidden">{c.name}</span>
          <label className="flex flex-col text-sm">
            {t("organize.categoryName")}
            <input value={c.name} onChange={(e) => update(i, { name: e.target.value })} className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--bg)] px-2 py-1" />
          </label>
          <label className="flex flex-col text-sm">
            {t("organize.categoryDescription")}
            <input value={c.description} onChange={(e) => update(i, { description: e.target.value })} className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--bg)] px-2 py-1" />
          </label>
          <div aria-label={t("organize.examples")} className="flex min-h-12 flex-wrap gap-1">
            {(c.examples ?? []).map((id) => (
              <img key={id} data-testid="example" src={mediaUrl(id, "t256")} alt="" className="h-12 w-12 rounded-[var(--radius-photo)] object-cover" />
            ))}
            {(c.examples ?? []).length === 0 && <span className="text-xs text-[var(--text-muted)]">{t("organize.dropHint")}</span>}
          </div>
          {hits && c.key in hits && (c.examples ?? []).length > 0 && (
            <p data-testid="library-hits" className="tnum text-sm">
              {t("organize.libraryHits", { n: hits[c.key].toLocaleString(i18n.language) })}
            </p>
          )}
        </article>
      ))}
      <button
        type="button"
        className="rounded-[var(--radius-md)] border border-[var(--border)] px-3 py-1.5"
        onClick={() => setCats([...cats, { key: `c${cats.length + 1}`, name: "", description: "", examples: [] }])}
      >
        {t("organize.addCategory")}
      </button>
    </section>
  );
}

function JobProgress({ job, onSkipped }: { job: Job; onSkipped: (planId: string) => void }) {
  const { t } = useTranslation();
  const skip = useMutation({
    mutationFn: async () => {
      await api.skipRest(job.id);
      const j = await api.job(job.id);
      onSkipped(j.plan_id!);
    },
  });
  return (
    <section className="space-y-4">
      <p className="flex items-center gap-3">
        <span>{t("organize.running")}</span>
        <span data-testid="source-status" className="rounded-[var(--radius-sm)] border border-[var(--border)] px-2 text-xs">
          {job.source_status === "offline" ? t("organize.sourceOffline") : t("organize.sourceOnline")}
        </span>
      </p>
      {job.waiting_reason && (
        <p data-testid="job-waiting" role="status">
          {job.waiting_reason === "SOURCE_OFFLINE" ? t("organize.waitingSource") : t(`waiting.${job.waiting_reason}.text`, { defaultValue: job.waiting_reason })}
        </p>
      )}
      {(job.reviews_pending ?? 0) > 0 && (
        <div data-testid="review-banner" className="flex items-center gap-3 rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--surface)] p-3">
          <span>{t("organize.reviewBanner", { n: job.reviews_pending })}</span>
          <a href={`/review?job=${job.id}`} className="rounded-[var(--radius-md)] bg-[var(--accent)] px-3 py-1.5 text-[var(--accent-fg)]">
            {t("organize.startReview")}
          </a>
          {job.status === "awaiting_approval" && (
            <button type="button" onClick={() => skip.mutate()} className="rounded-[var(--radius-md)] border border-[var(--border)] px-3 py-1.5">
              {t("organize.skipAllToOther")}
            </button>
          )}
        </div>
      )}
    </section>
  );
}

/** A refused action shows why and how to fix it (invariant 8): the server's preflight blockers as ProblemCards. */
export function Blockers({ error }: { error: unknown }) {
  const body = error instanceof ApiError ? (error.body as { detail?: { code?: string; blockers?: Problem[] } } | null) : null;
  const detail = body?.detail ?? (body as { code?: string; blockers?: Problem[] } | null);
  const list: Problem[] = detail?.blockers ?? (detail?.code ? [{ code: detail.code, params: {}, fixes: [] }] : [{ code: "INTERNAL", params: {}, fixes: [] }]);
  return (
    <div className="space-y-2" role="alert">
      {list.map((p, i) => (
        <ProblemCard key={`${p.code}-${i}`} problem={p} tone="danger" />
      ))}
    </div>
  );
}
