import { useEffect, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { groupOps, type Op, opTypeKey } from "./model";
import { OperationProgress } from "./OperationProgress";

/** 06 §6.1 Activity Center drawer: operations grouped by worker × lane; failed rows show the problem's fixes. */
export function ActivityCenter({
  open,
  ops,
  onClose,
  onToggle,
  onFix,
  actions,
  recent = [],
}: {
  open: boolean;
  ops: Op[];
  recent?: Array<Pick<Op, "id" | "type" | "status"> & { subject?: unknown; finished_at?: string | null }>;
  onClose: () => void;
  onToggle?: () => void;
  onFix?: (op: Op, fixId: string) => void;
  actions?: (op: Op) => ReactNode;
}) {
  const { t, i18n } = useTranslation();
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "j") {
        e.preventDefault();
        onToggle?.();
      }
      if (e.key === "Escape" && open) onClose();
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [open, onClose, onToggle]);
  if (!open) return null;
  return (
    <aside role="dialog" aria-modal="false" aria-label={t("activity.title")} className="activity-center">
      <h2 className="text-base font-semibold">{t("activity.title")}</h2>
      {ops.length === 0 ? <p className="text-[var(--text-muted)]">{t("activity.empty")}</p> : null}
      {groupOps(ops).map((g) => (
        <section key={g.key} aria-label={g.key}>
          <h3 className="text-xs text-[var(--text-muted)]">{g.key}</h3>
          <ul>
            {g.ops.map((op) => (
              <li key={op.id} data-op={op.id}>
                <span>{t(`activity.type.${opTypeKey(op)}`, { defaultValue: op.type })}</span>
                <OperationProgress op={op} variant="compact" />
                {op.auto_resumed ? <span className="text-xs text-[var(--text-muted)]">{t("activity.autoResumed")}</span> : null}
                {actions?.(op)}
                {op.status === "failed" && op.error ? (
                  <div role="group" aria-label={t(`errors.${op.error.code}.title`, { defaultValue: op.error.code })}>
                    <p>{t(`errors.${op.error.code}.message`, { defaultValue: op.error.code })}</p>
                    {(op.error.fixes ?? []).map((f) => (
                      <button key={f.id} type="button" onClick={() => onFix?.(op, f.id)}>
                        {t(`fix.${f.id}`, { defaultValue: f.id })}
                      </button>
                    ))}
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        </section>
      ))}
      {recent.length > 0 ? (
        // what just finished stays visible: the drawer went blank the moment a job ended (ACT-008)
        <section aria-labelledby="recent-h" className="space-y-1">
          <h3 id="recent-h" className="text-xs text-[var(--text-muted)]">{t("activity.recent")}</h3>
          <ul aria-labelledby="recent-h" className="space-y-1 text-sm">
            {recent.map((op) => (
              <li key={op.id} className="flex justify-between gap-2">
                <span>{t(`activity.type.${opTypeKey(op)}`, { defaultValue: op.type })}</span>
                <span className="tnum text-xs text-[var(--text-muted)]">
                  {t(`activity.status.${op.status}`, { defaultValue: op.status })}
                  {op.finished_at ? ` · ${new Date(op.finished_at).toLocaleTimeString(i18n.language, { hour: "2-digit", minute: "2-digit" })}` : ""}
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </aside>
  );
}
