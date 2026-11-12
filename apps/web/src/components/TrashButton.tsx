// 「移到垃圾桶」 (TRASH-013; the user approved it on 2026-11-12): the chosen photos become a plan you approve on the
// plan page; their files then go to their own folder's .opagent-trash/ and 復原 brings them back (invariant 3).
// Nothing moves from here. A read-only folder says so instead of doing anything else (TRASH-004).
import { useMutation } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { ApiError, api } from "../lib/api";
import { ProblemCard } from "./ProblemCard";

type Refusal = { code?: string; roots?: string[]; detail?: { code?: string; roots?: string[] } };

export function TrashButton({ select, compact = false }: { select: () => Promise<{ id: string }>; compact?: boolean }) {
  const { t } = useTranslation();
  const nav = useNavigate();
  const go = useMutation({
    mutationFn: async () => api.selectionTrash((await select()).id),
    onSuccess: (r) => void nav({ to: "/plans/$planId", params: { planId: r.plan_id } }),
  });
  const body = go.error instanceof ApiError ? ((go.error.body ?? {}) as Refusal) : null;
  const refusal = body ? (body.detail ?? body) : null;
  return (
    <span className="relative inline-flex flex-col">
      <button
        type="button"
        aria-label={t("trash.move")}
        title={t("trash.move")}
        aria-busy={go.isPending}
        onClick={() => !go.isPending && go.mutate()}
        className={compact ? "icon-button" : "rounded-[var(--radius-sm)] px-2 py-1 text-sm hover:bg-[var(--surface-2)]"}
      >
        {compact ? (
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v6M14 11v6" />
          </svg>
        ) : (
          t("trash.move")
        )}
      </button>
      {go.isError ? (
        <span role="alert" className="trash-refusal">
          <ProblemCard problem={{ code: refusal?.code ?? "INTERNAL", params: { path: (refusal?.roots ?? []).join("、") }, fixes: [] }} tone="danger" />
        </span>
      ) : null}
    </span>
  );
}
