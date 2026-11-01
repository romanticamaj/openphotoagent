// Review queue (04 §3.7, 03 §3.3「其他／不確定」): one photo at a time; 1–9 pick a candidate, 0 = other, Enter accepts
// the suggestion, S skips; the next photo comes up automatically. New reviews from a running job join live (REV-005).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "../../components/Button";
import { api, mediaUrl, type Review } from "../../lib/api";
import { waitForJob } from "../organize/OrganizeWizard";

function candidateKeys(r: Review): string[] {
  const raw = r.suggested?.candidates ?? [];
  const keys = raw.map((c) => (typeof c === "string" ? c : (c.key ?? c.id ?? ""))).filter((k) => k && k !== "other");
  const first = r.suggested?.category;
  return [...new Set([...(first && first !== "other" ? [first] : []), ...keys])].slice(0, 9);
}

export function ReviewPage({ jobId }: { jobId: string }) {
  const { t, i18n } = useTranslation();
  const qc = useQueryClient();
  const nav = useNavigate();
  const [askSkip, setAskSkip] = useState(false);
  const [handled, setHandled] = useState<Set<string>>(() => new Set());
  const list = useQuery({ queryKey: ["reviews", jobId], queryFn: () => api.reviews(jobId), refetchInterval: 1000 });
  const preset = useQuery({ queryKey: ["preset", "by-content-12", i18n.language], queryFn: () => api.preset("by-content-12", i18n.language) });
  const job = useQuery({ queryKey: ["job", jobId], queryFn: () => api.job(jobId) });
  const names = new Map((preset.data?.categories ?? []).map((c) => [c.key, c.name]));
  const custom = (job.data?.params?.categories as Array<{ key: string; name: string }> | undefined) ?? [];
  for (const c of custom) names.set(c.key, c.name);
  // a refetch racing an action must not bring back a photo that was just handled
  const items = (list.data?.items ?? []).filter((r) => !handled.has(r.id));
  const cur = items[0];
  const cands = cur ? candidateKeys(cur) : [];

  const act = useMutation({
    mutationFn: ({ r, action, category }: { r: Review; action: "accept" | "correct" | "skip"; category?: string }) =>
      api.actReview(r.id, action, category),
    onMutate: ({ r }) => setHandled((h) => new Set(h).add(r.id)), // the next photo appears immediately
    onSettled: () => void qc.invalidateQueries({ queryKey: ["reviews", jobId] }),
  });
  const skipRest = useMutation({
    mutationFn: async () => {
      await api.skipRest(jobId);
      const j = await waitForJob(jobId, (x) => x.status === "awaiting_approval" && Boolean(x.plan_id));
      void nav({ to: "/plans/$planId", params: { planId: j.plan_id! } });
    },
  });

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (!cur || askSkip || (e.target as HTMLElement).tagName === "INPUT") return;
      if (e.key === "Enter") act.mutate({ r: cur, action: "accept" });
      else if (e.key === "0") act.mutate({ r: cur, action: "correct", category: "other" });
      else if (/^[1-9]$/.test(e.key) && cands[Number(e.key) - 1]) act.mutate({ r: cur, action: "correct", category: cands[Number(e.key) - 1] });
      else if (e.key.toLowerCase() === "s") act.mutate({ r: cur, action: "skip" });
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  });

  const remaining = Math.max(0, (list.data?.count ?? 0) - (list.data?.items ?? []).filter((r) => handled.has(r.id)).length);
  if (list.isSuccess && !cur) {
    return (
      <section className="space-y-4 p-8">
        <p>{t("review.none")}</p>
        {job.data?.plan_id && (
          <Button onClick={() => void nav({ to: "/plans/$planId", params: { planId: job.data!.plan_id! } })}>{t("review.backToPlan")}</Button>
        )}
      </section>
    );
  }
  return (
    <section className="grid h-full grid-cols-[1fr_320px] gap-4 p-4" aria-labelledby="review-h">
      <div className="flex items-center justify-center bg-[var(--canvas)]">
        {cur && (
          <img
            data-testid="review-photo"
            data-asset={cur.asset_id}
            src={mediaUrl(cur.asset_id, "p1024")}
            alt=""
            className="max-h-[80vh] max-w-full object-contain"
          />
        )}
      </div>
      <aside className="space-y-4">
        <h1 id="review-h" className="text-lg font-semibold">
          {t("review.title")} <span data-testid="review-remaining" className="tnum text-[var(--text-muted)]">{t("review.remaining", { n: remaining })}</span>
        </h1>
        <p className="text-xs text-[var(--text-muted)]">{t("review.keys")}</p>
        {cur && (
          <ol className="space-y-1">
            {cands.map((k, i) => (
              <li key={k}>
                <button type="button" className="w-full rounded-[var(--radius-sm)] px-2 py-1 text-left hover:bg-[var(--surface-2)]" onClick={() => act.mutate({ r: cur, action: "correct", category: k })}>
                  <kbd className="tnum">{i + 1}</kbd> {names.get(k) ?? k}
                  {i === 0 ? ` · ${t("review.suggestion")} ${Math.round((cur.suggested?.confidence ?? 0) * 100)}%` : ""}
                </button>
              </li>
            ))}
            <li>
              <button type="button" className="w-full rounded-[var(--radius-sm)] px-2 py-1 text-left hover:bg-[var(--surface-2)]" onClick={() => act.mutate({ r: cur, action: "correct", category: "other" })}>
                <kbd>0</kbd> {t("review.other")}
              </button>
            </li>
          </ol>
        )}
        {cur && (
          <div className="flex flex-wrap gap-2">
            <Button onClick={() => act.mutate({ r: cur, action: "accept" })}>{t("review.accept")}</Button>
            <Button variant="ghost" onClick={() => act.mutate({ r: cur, action: "skip" })}>
              {t("review.skip")}
            </Button>
          </div>
        )}
        <Button variant="ghost" onClick={() => setAskSkip(true)}>
          {t("review.skipRest", { n: remaining })}
        </Button>
      </aside>
      {askSkip && (
        <div role="dialog" aria-modal="true" aria-labelledby="skip-h" className="fixed inset-0 z-40 grid place-items-center bg-black/50" onKeyDown={(e) => e.key === "Escape" && setAskSkip(false)}>
          <div className="max-w-md space-y-4 rounded-[var(--radius-lg)] bg-[var(--surface)] p-6">
            <h2 id="skip-h" className="text-lg font-semibold">{t("review.skipRestTitle")}</h2>
            <p>{t("review.skipRestBody", { n: remaining })}</p>
            <div className="flex gap-2">
              <Button autoFocus disabledReason={skipRest.isPending ? { code: "BUSY", message: t("common.working") } : null} onClick={() => skipRest.mutate()}>
                {t("organize.skipAllToOther")}
              </Button>
              <Button variant="ghost" onClick={() => setAskSkip(false)}>
                {t("organize.close")}
              </Button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
