// Activity Center wiring (06 §6.1): top-bar indicator, ⌘J drawer, row actions (resume / cancel with modes),
// and the recovery card after a restart (OP-006).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "../../components/Button";
import { ApiError, api } from "../../lib/api";
import { formatDuration } from "../../lib/format";
import { ActivityCenter } from "./ActivityCenter";
import { fromApi, indicatorState, type Op } from "./model";

export function useActivity() {
  return useQuery({
    queryKey: ["operations", "active"],
    queryFn: async () => (await api.operations("status=active")).map((o) => fromApi(o as unknown as Op)),
    refetchInterval: 1000,
  });
}

export function ActivityButton({ onToggle }: { onToggle: () => void }) {
  const { t } = useTranslation();
  const ops = useActivity().data ?? [];
  const ind = indicatorState(ops);
  return (
    <button data-testid="activity-button" type="button" onClick={onToggle} aria-label={`${t("activity.open")}：${t(ind.labelKey)}`} className="ml-auto flex items-center gap-1 rounded-[var(--radius-sm)] px-2 py-1 text-sm hover:bg-[var(--surface-2)]">
      <span aria-hidden="true" style={{ color: `var(${ind.token})` }}>
        {{ check: "✓", spinner: "◌", pause: "⏸", alert: "⚠", "plug-off": "⏏" }[ind.icon] ?? "•"}
      </span>
      {t("activity.open")}
    </button>
  );
}

export function ActivityDrawer({ open, setOpen }: { open: boolean; setOpen: (v: boolean) => void }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const ops = useActivity().data ?? [];
  const [cancelling, setCancelling] = useState<Op | null>(null);
  const refresh = () => void qc.invalidateQueries({ queryKey: ["operations"] });
  const resume = useMutation({ mutationFn: (id: string) => api.resumeOp(id), onSuccess: refresh });
  const cancel = useMutation({ mutationFn: (id: string) => api.cancelOp(id), onSuccess: refresh });
  return (
    <>
      <ActivityCenter
        open={open}
        ops={ops}
        onClose={() => setOpen(false)}
        onToggle={() => setOpen(!open)}
        actions={(op) => (
          <span className="flex gap-2">
            {["paused", "interrupted"].includes(op.status) && (
              <button type="button" className="text-sm underline" onClick={() => resume.mutate(op.id)}>
                {t("activity.resume")}
              </button>
            )}
            {["running", "queued", "waiting"].includes(op.status) && (
              <button type="button" className="text-sm underline" onClick={() => (op.type === "job.label" ? setCancelling(op) : cancel.mutate(op.id))}>
                {t("activity.cancel")}
              </button>
            )}
          </span>
        )}
      />
      {cancelling && <CancelDialog op={cancelling} onDone={() => (setCancelling(null), refresh())} />}
    </>
  );
}

/** graceful waits for the current batch; immediate gives leases back, confirming when > 1 min of work is lost. */
function CancelDialog({ op, onDone }: { op: Op; onDone: () => void }) {
  const { t, i18n } = useTranslation();
  const [discard, setDiscard] = useState<{ n: number; discard_s: number } | null>(null);
  const go = useMutation({
    mutationFn: async ({ mode, confirm }: { mode: "graceful" | "immediate"; confirm?: boolean }) => {
      try {
        await api.cancelJobMode(op.id, mode, confirm);
        onDone();
      } catch (e) {
        const b = e instanceof ApiError ? (e.body as { code?: string; n?: number; discard_s?: number } | null) : null;
        if (b?.code === "CANCEL_DISCARDS_WORK") setDiscard({ n: b.n ?? 0, discard_s: b.discard_s ?? 0 });
        else throw e;
      }
    },
  });
  const busy = go.isPending ? { code: "BUSY", message: t("common.working") } : null;
  return (
    <div role="dialog" aria-modal="true" aria-labelledby="cancel-h" className="fixed inset-0 z-50 grid place-items-center bg-black/50" onKeyDown={(e) => e.key === "Escape" && onDone()}>
      <div className="max-w-md space-y-4 rounded-[var(--radius-lg)] bg-[var(--surface)] p-6">
        <h2 id="cancel-h" className="text-lg font-semibold">{t("activity.cancelTitle")}</h2>
        {discard ? (
          <>
            <p>{t("activity.discard", { n: discard.n, time: formatDuration(discard.discard_s * 1000, i18n.language) })}</p>
            <div className="flex gap-2">
              <Button disabledReason={busy} onClick={() => go.mutate({ mode: "immediate", confirm: true })}>
                {t("activity.confirmImmediate")}
              </Button>
              <Button variant="ghost" onClick={onDone}>
                {t("activity.keep")}
              </Button>
            </div>
          </>
        ) : (
          <div className="flex flex-col gap-2">
            <Button autoFocus disabledReason={busy} onClick={() => go.mutate({ mode: "graceful" })}>
              {t("activity.graceful")}
            </Button>
            <Button variant="ghost" disabledReason={busy} onClick={() => go.mutate({ mode: "immediate" })}>
              {t("activity.immediate")}
            </Button>
            <Button variant="ghost" onClick={onDone}>
              {t("activity.keep")}
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}

/** After a restart: what was interrupted, resumed automatically or waits for the person (OP-006, ACT-004). */
export function RecoveryCard({ onReview }: { onReview: () => void }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [dismissed, setDismissed] = useState(false);
  const ops = (useActivity().data ?? []).filter((o) => o.status === "interrupted" || o.auto_resumed);
  const resumeAll = useMutation({
    mutationFn: async () => {
      for (const o of ops.filter((x) => x.status === "interrupted")) await api.resumeOp(o.id);
    },
    onSuccess: () => {
      setDismissed(true);
      void qc.invalidateQueries({ queryKey: ["operations"] });
    },
  });
  if (dismissed || ops.length === 0) return null;
  return (
    <section data-testid="recovery-card" aria-labelledby="recovery-h" className="m-4 space-y-2 rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--surface)] p-4">
      <h2 id="recovery-h" className="font-semibold">{t("activity.recoveryTitle", { n: ops.length })}</h2>
      <ul className="text-sm">
        {ops.map((o) => (
          <li key={o.id}>
            {t(`activity.type.${o.type}`, { defaultValue: o.type })} · {o.auto_resumed ? t("activity.autoResumed") : t(`activity.status.${o.status}`)}
          </li>
        ))}
      </ul>
      <div className="flex gap-2">
        <Button disabledReason={resumeAll.isPending ? { code: "BUSY", message: t("common.working") } : null} onClick={() => resumeAll.mutate()}>
          {t("activity.resumeAll")}
        </Button>
        <Button variant="ghost" onClick={onReview}>
          {t("activity.review")}
        </Button>
      </div>
    </section>
  );
}
