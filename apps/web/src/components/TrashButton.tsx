// 「移到垃圾桶」 (TRASH-013; the user approved it on 2026-11-12): the chosen photos become a plan you approve on the
// plan page; their files then go to their own folder's .opagent-trash/ and 復原 brings them back (invariant 3).
// Nothing moves from here. A read-only folder is said up front when every folder is read-only (TRASH-016), and a
// refusal says why, that nothing changed, and what to do instead, beside the button (TRASH-015, invariant 8).
import { useMutation, useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiError, api } from "../lib/api";

type Refusal = { code?: string; roots?: string[]; detail?: { code?: string; roots?: string[] } };

export function TrashButton({ select, compact = false }: { select: () => Promise<{ id: string }>; compact?: boolean }) {
  const { t } = useTranslation();
  const nav = useNavigate();
  const uid = useId().replace(/:/g, "");
  const roots = useQuery({ queryKey: ["roots"], queryFn: api.roots });
  const readOnly = roots.isSuccess && (roots.data ?? []).length > 0 && (roots.data ?? []).every((r) => r.mode !== "rw");
  const [shown, setShown] = useState<string[] | null>(null); // the folders a refusal names, while its card is open
  const go = useMutation({
    mutationFn: async () => api.selectionTrash((await select()).id),
    onSuccess: (r) => void nav({ to: "/plans/$planId", params: { planId: r.plan_id } }),
    onError: (e) => {
      const body = e instanceof ApiError ? ((e.body ?? {}) as Refusal) : null;
      const refusal = body ? (body.detail ?? body) : null;
      setShown(refusal?.code === "FS_NOT_WRITABLE" ? (refusal.roots ?? []) : []);
    },
  });
  const why = readOnly ? t("trash.allReadOnly") : null;
  const press = () => {
    if (readOnly) return setShown((roots.data ?? []).map((r) => r.alias)); // said before any request (TRASH-016)
    if (!go.isPending) go.mutate();
  };
  return (
    <span className="relative inline-flex flex-col">
      <button
        type="button"
        data-trash=""
        aria-label={t("trash.move")}
        title={why ?? t("trash.move")}
        aria-busy={go.isPending}
        aria-disabled={readOnly || undefined}
        aria-describedby={why ? `why-${uid}` : undefined}
        onClick={press}
        className={`${compact ? "icon-button" : "rounded-[var(--radius-sm)] border border-[var(--border)] px-3 py-1 text-sm hover:bg-[var(--surface-2)]"} aria-disabled:opacity-50`}
      >
        {compact ? (
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v6M14 11v6" />
          </svg>
        ) : (
          t("trash.move")
        )}
      </button>
      {why ? (
        <span id={`why-${uid}`} className={compact ? "sr-only" : "text-xs text-[var(--text-muted)]"}>
          {why}
        </span>
      ) : null}
      {shown !== null ? (
        // floats beside the button: the selection bar keeps its height, the Lightbox does not clip it (TRASH-015)
        <span className={compact ? "trash-popover trash-popover-end" : "trash-popover"}>
          <span role="alert" className="space-y-1">
            <strong className="block">
              {shown.length ? t("trash.readOnly", { roots: shown.map((r) => `「${r}」`).join("、") }) : t("trash.failed")}
            </strong>
            <span className="block text-sm">{t("trash.nothingMoved")}</span>
            {shown.length ? (
              <span className="block text-sm">
                <Link to="/import" className="underline">
                  {t("trash.reAdd")}
                </Link>{" "}
                {t("trash.orCopy")}
              </span>
            ) : null}
          </span>
          <button type="button" className="trash-popover-close" aria-label={t("trash.close")} onClick={() => setShown(null)}>
            ×
          </button>
        </span>
      ) : null}
    </span>
  );
}
