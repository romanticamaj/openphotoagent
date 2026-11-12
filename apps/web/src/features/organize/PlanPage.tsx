// Output plan (04 §3.5): one-sentence summary + expandable folder tree → approve (moves need a second confirmation,
// > 10,000 moves a typed string) → applying with cancel → outcome (renamed / identical skipped) → undo.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "../../components/Button";
import { api, type Plan, type PlanAction } from "../../lib/api";
import { platform } from "../../lib/platform";
import { Blockers } from "./OrganizeWizard";

const MOVE_TEXT_ABOVE = 10_000;
const FINAL = new Set(["applied", "partially_applied", "cancelled", "failed", "undone"]);

export function PlanPage({ planId }: { planId: string }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const plan = useQuery({
    queryKey: ["plan", planId],
    queryFn: () => api.plan(planId),
    refetchInterval: (q) => (q.state.data && (q.state.data.status === "draft" || FINAL.has(q.state.data.status)) ? false : 300),
  });
  const p = plan.data;
  const moves = p?.actions?.filter((a) => a.action === "move").length ?? 0;
  const approve = useMutation({
    mutationFn: (body: { move_confirmed?: boolean; confirm_text?: string }) => api.approve(planId, body),
    onSuccess: () => {
      setConfirming(false);
      void qc.invalidateQueries({ queryKey: ["plan", planId] });
    },
  });
  const cancel = useMutation({ mutationFn: () => api.cancelJob(p!.job_id) });
  // 捨棄計畫 (ORG-020): a plan you do not want; nothing on disk changes (ADR-0045)
  const discard = useMutation({
    mutationFn: () => api.discardPlan(planId),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["plan", planId] }),
  });
  // the button pressed goes away: focus to the way back (audit round 58: it fell to the page)
  const backRef = useRef<HTMLAnchorElement>(null);
  const discardedNow = Boolean(plan.data?.discarded);
  useEffect(() => {
    if (discardedNow && discard.isSuccess) backRef.current?.focus();
  }, [discardedNow, discard.isSuccess]);
  const undo = useMutation({
    mutationFn: () => api.undo(planId),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["plan", planId] }),
  });
  if (!p) return <p role="status">{t("common.working")}</p>;
  const plat = platform();
  const target = p.actions?.[0]?.dst_alias ?? "";
  const status = p.status;

  return (
    <section className="mx-auto max-w-3xl space-y-6 p-8">
      <h1 className="text-xl font-semibold">{t("organize.planTitle")}</h1>
      <p data-testid="plan-status" className="text-sm text-[var(--text-muted)]">
        {p.discarded ? t("organize.planStatus.discarded") : t(`organize.planStatus.${status}`, { defaultValue: status })}
      </p>
      {status === "draft" && p.discarded ? (
        <div data-testid="plan-discarded" role="status" className="space-y-2">
          <p>{t("organize.discarded")}</p>
          <Link ref={backRef} to="/organize" className="underline">
            {t("organize.backToOrganize")}
          </Link>
        </div>
      ) : null}
      {status === "draft" && !p.discarded && (
        <>
          <p data-testid="plan-summary" className="text-lg">
            {p.summary.trash
              ? // photos you chose are not duplicates: no 「保留代表張」 for them (TRASH-014)
                t(p.summary.kind === "trash" ? "organize.summaryTrashChosen" : "organize.summaryTrash", { count: p.summary.trash })
              : moves
                ? t("organize.summaryMove", { count: moves, folders: p.summary.folders })
                : t("organize.summaryCopy", { count: p.summary.copy, folders: p.summary.folders })}
          </p>
          <PlanTree actions={p.actions ?? []} />
          <Button
            disabledReason={approve.isPending ? { code: "BUSY", message: t("common.working") } : null}
            onClick={() => (moves ? setConfirming(true) : approve.mutate({}))}
          >
            {t("organize.approve")}
          </Button>
          <Button variant="ghost" disabledReason={discard.isPending ? { code: "BUSY", message: t("common.working") } : null} onClick={() => discard.mutate()}>
            {t("organize.discard")}
          </Button>
          {approve.error && <Blockers error={approve.error} />}
        </>
      )}
      {status === "applying" && (
        <div className="space-y-3">
          <p role="status">{t("organize.applying")}</p>
          <Progress plan={p} />
          <Button variant="ghost" disabledReason={cancel.isPending ? { code: "BUSY", message: t("common.working") } : null} onClick={() => cancel.mutate()}>
            {t("organize.cancel")}
          </Button>
        </div>
      )}
      {(status === "applied" || status === "partially_applied" || status === "cancelled") && (
        <div className="space-y-4">
          {status === "applied" && (
            <h2 data-testid="done-title" className="text-xl font-semibold">
              {t("organize.doneTitle")}
            </h2>
          )}
          {p.outcome && (
            <p data-testid="outcome">
              {t("organize.outcomeDone", { n: p.outcome.done })}
              {p.outcome.renamed || p.outcome.same_skipped
                ? ` · ${t("organize.outcome", { renamed: p.outcome.renamed, same: p.outcome.same_skipped })}`
                : ""}
            </p>
          )}
          <PlanTree actions={p.actions ?? []} />
          <div className="flex flex-wrap gap-3">
            {plat.kind === "desktop" && plat.revealInFileManager && (
              <Button variant="ghost" onClick={() => void plat.revealInFileManager!(target, "")}>
                {t("organize.openInFileManager")}
              </Button>
            )}
            <Link to="/search" search={{ q: "" }} className="rounded-[var(--radius-md)] border border-[var(--border)] px-4 py-2 text-sm">
              {t("organize.trySearch")}
            </Link>
            {status !== "cancelled" && (
              <Button variant="ghost" disabledReason={undo.isPending ? { code: "BUSY", message: t("common.working") } : null} onClick={() => undo.mutate()}>
                {t("organize.undo")}
              </Button>
            )}
          </div>
        </div>
      )}
      {status === "undoing" && <p role="status">{t("organize.planStatus.undoing")}</p>}
      {status === "undone" && (
        <h2 data-testid="undone-title" className="text-xl font-semibold">
          {t("organize.undoneTitle")}
        </h2>
      )}
      {confirming && <MoveDialog n={moves} pending={approve.isPending} onCancel={() => setConfirming(false)} onConfirm={(text) => approve.mutate({ move_confirmed: true, confirm_text: text })} />}
    </section>
  );
}

function Progress({ plan }: { plan: Plan }) {
  const total = plan.actions?.length ?? 0;
  const done = plan.actions?.filter((a) => a.status !== "planned").length ?? 0;
  const pct = total ? Math.round((100 * done) / total) : 0;
  return (
    <div role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct} aria-label="progress" className="h-1.5 w-full rounded bg-[var(--surface-2)]">
      <div className="h-full rounded bg-[var(--accent)]" style={{ width: `${pct}%` }} />
    </div>
  );
}

/** Folder tree of the plan, collapsed by default (04 §3.5). */
function PlanTree({ actions }: { actions: PlanAction[] }) {
  const { t } = useTranslation();
  const groups = new Map<string, PlanAction[]>();
  for (const a of actions.slice(0, 2000)) {
    const dir = a.dst_rel.includes("/") ? a.dst_rel.slice(0, a.dst_rel.lastIndexOf("/")) : ".";
    if (!groups.has(dir)) groups.set(dir, []);
    groups.get(dir)!.push(a);
  }
  return (
    <section data-testid="plan-tree" aria-label={t("organize.planTree")} className="space-y-1 text-sm">
      {[...groups].map(([dir, list]) => (
        <details key={dir}>
          <summary className="cursor-pointer">
            {dir} <span className="tnum text-[var(--text-muted)]">({list.length})</span>
          </summary>
          <ul className="ml-4">
            {list.map((a) => (
              <li key={a.id} className="font-mono text-xs">
                {a.dst_rel.slice(a.dst_rel.lastIndexOf("/") + 1)}
                {a.conflict === "exists_diff" ? " ↺" : a.conflict === "exists_same" ? " =" : ""}
              </li>
            ))}
          </ul>
        </details>
      ))}
    </section>
  );
}

function MoveDialog({ n, pending, onCancel, onConfirm }: { n: number; pending: boolean; onCancel: () => void; onConfirm: (text?: string) => void }) {
  const { t, i18n } = useTranslation();
  const [ack, setAck] = useState(false);
  const [text, setText] = useState("");
  const expected = `MOVE ${n}`;
  const needText = n > MOVE_TEXT_ABOVE;
  const reason = !ack
    ? { code: "CONFIRM_REQUIRED", message: t("organize.moveNeedAck") }
    : needText && text !== expected
      ? { code: "CONFIRM_TEXT_REQUIRED", message: t("organize.moveNeedText") }
      : pending
        ? { code: "BUSY", message: t("common.working") }
        : null;
  return (
    <div role="dialog" aria-modal="true" aria-labelledby="move-title" className="fixed inset-0 z-40 grid place-items-center bg-black/50" onKeyDown={(e) => e.key === "Escape" && onCancel()}>
      <div className="max-w-md space-y-4 rounded-[var(--radius-lg)] bg-[var(--surface)] p-6">
        <h2 id="move-title" className="text-lg font-semibold">
          {t("organize.moveTitle")}
        </h2>
        <p>{t("organize.moveBody", { n: n.toLocaleString(i18n.language) })}</p>
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} autoFocus />
          {t("organize.moveAck")}
        </label>
        {needText && (
          <label className="flex flex-col gap-1 text-sm">
            {t("organize.moveTypeLabel", { expected })}
            <input value={text} onChange={(e) => setText(e.target.value)} className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--bg)] px-2 py-1 font-mono" />
          </label>
        )}
        <div className="flex gap-2">
          <Button id="move-go" disabledReason={reason} onClick={() => onConfirm(needText ? text : undefined)}>
            {t("organize.moveGo")}
          </Button>
          <Button variant="ghost" onClick={onCancel}>
            {t("organize.close")}
          </Button>
        </div>
      </div>
    </div>
  );
}
