import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "../../components/Button";
import { FolderPicker } from "../../components/FolderPicker";
import { ProblemCard, type Problem } from "../../components/ProblemCard";
import { ApiError, api, type Job, type Plan } from "../../lib/api";

type Step = "folder" | "method" | "preview" | "applying" | "done" | "undone";

async function waitFor(jobId: string, statuses: string[]): Promise<Job> {
  for (;;) {
    const j = await api.job(jobId);
    if (statuses.includes(j.status)) return j;
    if (j.status === "failed") throw new Error("job failed");
    await new Promise((r) => setTimeout(r, 250));
  }
}

export function OrganizeWizard() {
  const { t } = useTranslation();
  const [step, setStep] = useState<Step>("folder");
  const [folder, setFolder] = useState<{ root: string; path: string } | null>(null);
  const [method, setMethod] = useState<"by-date" | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [plan, setPlan] = useState<Plan | null>(null);
  const roots = useQuery({ queryKey: ["roots"], queryFn: api.roots });
  const target = (roots.data ?? []).find((r) => r.mode === "rw");

  const preview = useMutation({
    mutationFn: async () => {
      const src = await api.createSource(folder!.root, folder!.path);
      const j = await api.createJob(src.id, method!, { root_alias: target!.alias, sub_path: "" });
      const ready = await waitFor(j.id, ["awaiting_approval"]);
      setJob(ready);
      setPlan(await api.plan(ready.plan_id!));
      setStep("preview");
    },
  });
  const approve = useMutation({
    mutationFn: async () => {
      setStep("applying");
      await api.approve(plan!.id);
      await waitFor(job!.id, ["completed"]);
      setStep("done");
    },
  });
  const undo = useMutation({
    mutationFn: async () => {
      await api.undo(plan!.id);
      await waitFor(job!.id, ["undone"]);
      setStep("undone");
    },
  });

  if (step === "folder") {
    return <FolderPicker onPick={(root, path) => { setFolder({ root, path }); setStep("method"); }} />;
  }
  if (step === "method") {
    const previewReason = !method
      ? { code: "METHOD_REQUIRED", message: t("organize.pickMethodFirst") }
      : !target
        ? { code: "FS_NOT_WRITABLE", message: t("organize.noWritableTarget") }
        : preview.isPending
          ? { code: "BUSY", message: t("common.working") }
          : null;
    return (
      <section className="space-y-4">
        <fieldset className="space-y-2">
          <legend className="text-lg font-semibold">{t("organize.method")}</legend>
          <label className="flex items-center gap-2">
            <input type="radio" name="method" value="by-date" checked={method === "by-date"} onChange={() => setMethod("by-date")} />
            <span>{t("organize.byDate")}</span>
            <span className="text-sm text-[var(--color-muted)]">{t("organize.byDateHint")}</span>
          </label>
        </fieldset>
        <p>
          {t("organize.output")}: <strong data-testid="output-target">{target?.alias ?? "—"}</strong>
        </p>
        <Button disabledReason={previewReason} onClick={() => preview.mutate()}>{t("organize.preview")}</Button>
        {preview.error && <Blockers error={preview.error} />}
      </section>
    );
  }
  if (step === "preview" && plan) {
    return (
      <section className="space-y-4">
        <p data-testid="plan-summary" className="text-lg">
          {t("organize.summaryCopy", { count: plan.summary.copy, folders: plan.summary.folders })}
        </p>
        <Button disabledReason={approve.isPending ? { code: "BUSY", message: t("common.working") } : null} onClick={() => approve.mutate()}>
          {t("organize.approve")}
        </Button>
      </section>
    );
  }
  if (step === "applying") return <p role="status">{t("organize.applying")}</p>;
  if (step === "done") {
    return (
      <section className="space-y-4">
        <h2 data-testid="done-title" className="text-xl font-semibold">{t("organize.doneTitle")}</h2>
        <Button variant="ghost" disabledReason={undo.isPending ? { code: "BUSY", message: t("common.working") } : null} onClick={() => undo.mutate()}>
          {t("organize.undo")}
        </Button>
      </section>
    );
  }
  return <h2 data-testid="undone-title" className="text-xl font-semibold">{t("organize.undoneTitle")}</h2>;
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
