// Onboarding (04 §8.2, 06 §6.6): welcome → one-sentence hardware conclusion + one recommended combination →
// downloads in the background while the user picks a folder → scanning collage. No account, org or workspace.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "../../components/Button";
import { FolderPicker } from "../../components/FolderPicker";
import { CompatBadge } from "../../components/CompatBadge";
import { ProblemCard, type Fix } from "../../components/ProblemCard";
import { api, mediaUrl, type Operation, type Recommendation } from "../../lib/api";
import { formatBytes } from "../../lib/format";
import { OperationProgress } from "../activity/OperationProgress";
import { fromApi } from "../activity/model";

type Step = "welcome" | "plan" | "folder";

function runtimeOf(r: Recommendation, chosen: string | null): string {
  const p = chosen ?? r.combo.packs[0] ?? "";
  if (p.includes("vulkan")) return "vulkan";
  if (p.includes("cuda")) return "cuda";
  if (r.hardware.os === "macos") return "metal";
  return "cpu";
}

export function Welcome() {
  const { t, i18n } = useTranslation();
  const [step, setStep] = useState<Step>("welcome");
  const [pack, setPack] = useState<string | null>(null);
  const roots = useQuery({ queryKey: ["roots"], queryFn: api.roots });
  const workerId = roots.data?.[0]?.worker_id;
  const reco = useQuery({
    queryKey: ["recommendation", workerId],
    queryFn: () => api.recommendation(workerId!),
    enabled: Boolean(workerId) && step !== "welcome",
  });
  const vlmKey = reco.data?.combo.variants.find((k) => !k.startsWith("siglip"));
  const vlm = useQuery({
    queryKey: ["model", vlmKey?.split("@")[0], workerId],
    queryFn: () => api.model(vlmKey!.split("@")[0], workerId!),
    enabled: Boolean(vlmKey && workerId),
  });
  const vlmCompat = vlm.data?.variants.find((v) => v.key === vlmKey)?.compat;
  const install = useMutation({
    mutationFn: () => api.installCombo(workerId!, reco.data!.combo.variants),
    onSuccess: () => setStep("folder"),
  });

  if (step === "welcome") {
    return (
      <section className="mx-auto max-w-xl space-y-6 p-12 text-center">
        <h1 className="text-3xl font-semibold">{t("onboarding.welcome")}</h1>
        <p className="text-[var(--text-muted)]">{t("onboarding.tagline")}</p>
        <Button onClick={() => setStep("plan")}>{t("onboarding.start")}</Button>
      </section>
    );
  }
  if (step === "plan") {
    const r = reco.data;
    if (!r) return <p className="p-12" role="status">{t("onboarding.checking")}</p>;
    const warnings = r.warnings.filter((w) => !(pack && w.code === "GPU_DRIVER_TOO_OLD"));
    const onFix = (f: Fix) => {
      if (f.type === "choose_alternative" && f.pack) setPack(String(f.pack));
    };
    const fastOrStd = r.mode === "fast" ? "fast" : "standard";
    return (
      <section className="mx-auto max-w-2xl space-y-6 p-12" aria-labelledby="hw-title">
        <h1 id="hw-title" className="text-2xl font-semibold">{t("onboarding.hwTitle")}</h1>
        <p data-testid="hw-conclusion" className="text-lg">
          {t(`onboarding.conclusion.${r.mode}`, { gpu: r.hardware.gpu ?? "", vram: Math.round(r.hardware.vram_gb ?? 0) })}
        </p>
        {warnings.map((w) => (
          <ProblemCard key={w.code} problem={w} onFix={onFix} />
        ))}
        {pack && <p role="status">{t("problem.chosen", { what: t(`onboarding.plan.${runtimeOf(r, pack)}`) })}</p>}
        <article data-testid="plan-card" className="space-y-2 rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--surface)] p-5">
          <h2 className="text-lg font-semibold">{t(`onboarding.plan.${fastOrStd}`)}</h2>
          {vlmKey && (
            <p className="flex items-center gap-2 text-sm">
              {vlm.data?.display_name ?? vlmKey} <CompatBadge compat={vlmCompat} />
            </p>
          )}
          <p className="tnum">
            {t("onboarding.plan.download", { size: formatBytes(r.combo.download_bytes, i18n.language), min: r.combo.download_minutes })}
          </p>
          {r.per_1000_minutes && (
            <p className="tnum" data-testid="per-1000">
              {t("onboarding.plan.per1000", { lo: r.per_1000_minutes[0], hi: r.per_1000_minutes[1] })}
            </p>
          )}
          <p>{t("onboarding.plan.runtime", { runtime: t(`onboarding.plan.${runtimeOf(r, pack)}`) })}</p>
          <p data-testid="license-line" className="text-sm text-[var(--text-muted)]">
            {t("onboarding.plan.license", { licenses: r.licenses.join("、") })}
          </p>
        </article>
        <Button disabledReason={install.isPending ? { code: "BUSY", message: t("common.working") } : null} onClick={() => install.mutate()}>
          {t("onboarding.download")}
        </Button>
      </section>
    );
  }
  return <FolderStep />;
}

function FolderStep() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [source, setSource] = useState<string | null>(null);
  const ops = useQuery({ queryKey: ["operations", "downloads"], queryFn: () => api.operations("status=all&type=model.download"), refetchInterval: 1000 });
  const scan = useMutation({
    mutationFn: async ({ root, path }: { root: string; path: string }) => {
      const s = await api.createSource(root, path);
      await api.scanSource(s.id);
      setSource(s.id);
    },
  });
  const wall = useQuery({
    queryKey: ["collage", source],
    enabled: Boolean(source),
    refetchInterval: 1000,
    queryFn: async () => {
      const b = await api.buckets();
      const out = [];
      for (const x of b.buckets) {
        out.push(...(await api.bucket(x.month)).assets);
        if (out.length >= 60) break;
      }
      void qc.invalidateQueries({ queryKey: ["timeline"] });
      return out.slice(0, 60);
    },
  });
  const downloads: Operation[] = ops.data ?? [];
  const problems = downloads.filter((o) => o.error);
  return (
    <section className="mx-auto max-w-4xl space-y-6 p-12">
      <div className="space-y-2" data-testid="download-list" aria-label={t("onboarding.downloads")}>
        <h2 className="text-sm font-semibold text-[var(--text-muted)]">{t("onboarding.downloads")}</h2>
        {downloads.map((o) => (
          <div key={o.id} className="flex items-center gap-3 text-sm">
            <span className="w-64 truncate">{o.subject?.id?.startsWith("siglip") ? "SigLIP2" : o.subject?.id?.split("@")[0]}</span>
            <OperationProgress op={fromApi(o)} variant="inline" />
          </div>
        ))}
        {problems.map((o) => (
          <ProblemCard
            key={o.id}
            problem={o.error!}
            tone="danger"
            onFix={(f) => {
              if (f.type === "retry") void api.operations(); // retries are automatic (06 §2.9); refresh the view
            }}
          />
        ))}
      </div>
      {!source ? (
        <>
          <p className="text-[var(--text-muted)]">{t("onboarding.pickHint")}</p>
          <FolderPicker title={t("onboarding.pickTitle")} onPick={(root, path) => scan.mutate({ root, path })} />
        </>
      ) : (
        <section aria-labelledby="collage-h" className="space-y-3">
          <h2 id="collage-h" className="text-lg font-semibold">{t("onboarding.collage")}</h2>
          <div data-testid="scan-collage" className="grid grid-cols-6 gap-0.5">
            {(wall.data ?? []).map((a) => (
              <img key={a.id} src={mediaUrl(a.id, "t256", a.ch)} alt="" className="aspect-square w-full rounded-[var(--radius-photo)] object-cover" />
            ))}
          </div>
          {(wall.data ?? []).length === 0 && <p role="status">{t("onboarding.scanning")}</p>}
          <div className="flex gap-3">
            <Link to="/organize" className="rounded-[var(--radius-md)] bg-[var(--accent)] px-4 py-2 text-[var(--accent-fg)]">
              {t("onboarding.organizeThese")}
            </Link>
            <Link to="/" className="rounded-[var(--radius-md)] border border-[var(--border)] px-4 py-2">
              {t("onboarding.browse")}
            </Link>
          </div>
        </section>
      )}
    </section>
  );
}
