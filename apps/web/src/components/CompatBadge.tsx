// CompatBadge (06 §6.7): icon + text, never colour alone (A11Y-006). Every surface passes the same compat object
// from the API (MDL-005), so text and reason code match everywhere.
import { useTranslation } from "react-i18next";

export type Compat = { level: string; label_key?: string; reasons?: Array<{ code: string; params?: Record<string, unknown> }> };

const ICON: Record<string, string> = { ok: "●", degraded: "◐", blocked: "○" };
const TOKEN: Record<string, string> = { ok: "--success", degraded: "--warning", blocked: "--danger" };

export function CompatBadge({ compat }: { compat: Compat | null | undefined }) {
  const { t } = useTranslation();
  const level = compat?.level ?? "unknown";
  const reason = compat?.reasons?.[0];
  const why = reason ? t(`errors.${reason.code}.title`, { ...(reason.params ?? {}), defaultValue: reason.code }) : undefined;
  return (
    <span
      data-testid="compat-badge"
      data-level={level}
      data-reason={reason?.code ?? ""}
      title={why}
      className="inline-flex items-center gap-1 whitespace-nowrap text-sm"
    >
      <span aria-hidden="true" style={{ color: `var(${TOKEN[level] ?? "--text-muted"})` }}>
        {ICON[level] ?? "?"}
      </span>{" "}
      {t(`compat.${level}`)}
    </span>
  );
}
