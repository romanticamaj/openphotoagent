// Agent request approval (05 §3.6; MCP-APPR-006): who asked and when, the full plan, and the same confirmations as
// approving from the organize page — a move needs a second confirmation, a very large move a typed text.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "../../components/Button";
import { ApiError, api, type Approval } from "../../lib/api";
import { useServerEvent } from "../../lib/events";

export function ApprovalPage() {
  const { approvalId } = useParams({ strict: false }) as { approvalId: string };
  const { t, i18n } = useTranslation();
  const qc = useQueryClient();
  const a = useQuery({ queryKey: ["approval", approvalId], queryFn: () => api.approval(approvalId) });
  const plan = useQuery({ queryKey: ["plan", a.data?.plan_id], queryFn: () => api.plan(a.data!.plan_id), enabled: Boolean(a.data) });
  const [moveOk, setMoveOk] = useState(false);
  const [text, setText] = useState("");
  const decide = useMutation({
    mutationFn: (d: "approve" | "reject") => api.decideApproval(approvalId, { decision: d, move_confirmed: moveOk, confirm_text: text || null }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["approval", approvalId] }),
  });
  if (!a.data) return <p role="status" className="p-8">{t("common.loading")}</p>;
  const ap = a.data;
  const moves = ap.counts.move ?? 0;
  const err = decide.error instanceof ApiError ? (decide.error.body as { code?: string; expected?: string } | null) : null;
  const when = new Date(ap.requested_at).toLocaleTimeString(i18n.language, { hour: "2-digit", minute: "2-digit" });
  return (
    <section className="mx-auto max-w-3xl space-y-4 p-8" aria-labelledby="ap-title" data-testid="approval">
      <h1 id="ap-title" className="text-2xl font-semibold">{t(`approval.title.${ap.kind}`)}</h1>
      <p data-testid="approval-requester">
        {t("approval.requestedBy", { client: ap.client ?? "?", grant: ap.grant?.number ?? "?", time: when })}
      </p>
      {ap.note && <p className="text-sm text-[var(--text-muted)]">{t("approval.note")}：<span translate="no">{ap.note}</span></p>}
      <p className="tnum">
        {Object.entries(ap.counts).map(([k, n]) => t("approval.count", { action: t(`approval.action.${k}`, { defaultValue: k }), n })).join("、")}
      </p>
      {ap.status !== "pending" ? (
        <p role="status" data-testid="approval-status">{t(`approval.status.${ap.status}`)}</p>
      ) : (
        <>
          {plan.data && (
            <table className="w-full text-sm" data-testid="approval-actions">
              <tbody>
                {(plan.data.actions ?? []).slice(0, 200).map((x) => (
                  <tr key={x.id}>
                    <td>{t(`approval.action.${x.action}`, { defaultValue: x.action })}</td>
                    <td translate="no">{`${x.src_alias ?? ""}/${x.src_rel}`}</td>
                    <td translate="no">{`→ ${x.dst_alias}/${x.dst_rel}`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {moves > 0 && (
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={moveOk} onChange={(e) => setMoveOk(e.target.checked)} />
              {t("approval.confirmMove", { n: moves })}
            </label>
          )}
          {err?.code === "CONFIRM_TEXT_REQUIRED" && (
            <label className="block text-sm">
              {t("approval.typeToConfirm", { text: err.expected })}
              <input className="ml-2 rounded border border-[var(--border)] px-2" value={text} onChange={(e) => setText(e.target.value)} />
            </label>
          )}
          {err && err.code !== "CONFIRM_TEXT_REQUIRED" && <p role="alert">{t(`errors.${err.code}.title`, { defaultValue: err.code })}</p>}
          <div className="flex gap-2">
            <Button
              disabledReason={moves > 0 && !moveOk ? { code: "MOVE_NOT_CONFIRMED", message: t("approval.needMoveConfirm") } : null}
              onClick={() => decide.mutate("approve")}
            >
              {t("approval.approve")}
            </Button>
            <Button variant="ghost" onClick={() => decide.mutate("reject")}>{t("approval.reject")}</Button>
          </div>
        </>
      )}
      {decide.data?.status === "approved" && <Link to="/plans/$planId" params={{ planId: ap.plan_id }}>{t("approval.follow")}</Link>}
    </section>
  );
}

/** Agent request card (04 §3.10): pending requests from AI assistants, shown on every page until decided. */
export function ApprovalInbox() {
  const { t } = useTranslation();
  const q = useQuery({ queryKey: ["approvals"], queryFn: () => api.approvals(), refetchInterval: 30_000 });
  useServerEvent("approval.requested", () => void q.refetch());
  const pending: Approval[] = q.data ?? [];
  if (!pending.length) return null;
  return (
    <aside role="status" data-testid="approval-card" className="mb-4 rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--surface)] p-3 text-sm">
      {pending.slice(0, 3).map((x) => (
        <p key={x.id}>
          {t("approval.card", { client: x.client ?? "?", n: Object.values(x.counts).reduce((s, n) => s + n, 0) })}{" "}
          <Link to="/approvals/$approvalId" params={{ approvalId: x.id }}>{t("approval.open")}</Link>
        </p>
      ))}
    </aside>
  );
}
