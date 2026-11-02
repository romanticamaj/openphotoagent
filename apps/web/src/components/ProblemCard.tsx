// ProblemCard (06 §4.3): title, message and the fixes for one problem code — never just "retry".
import { useTranslation } from "react-i18next";

export type Fix = { id?: string; type: string; target?: string | null; url?: string; pack?: string; primary?: boolean; [k: string]: unknown };
export type Problem = { code: string; params?: Record<string, unknown>; fixes?: Fix[] };

export function fixLabelKey(code: string, f: Fix, exists: (k: string) => boolean): string {
  for (const k of [`fix.${code}.${f.type}`, f.id ? `fix.${f.id}` : "", f.target ? `fix.${f.target}` : "", `fix.${f.type}`]) {
    if (k && exists(k)) return k;
  }
  return `fix.${f.id ?? f.type}`;
}

type T = (k: string, o?: Record<string, unknown>) => string;
/** A fix button's text: a dedicated key if there is one, else the i-th item of the code's 06 §4.7 "修復" column
 *  (`errors.<code>.cta`, items separated by " · "), else a generic label for the fix type (I18N-007). */
export function fixLabel(problem: Problem, f: Fix, i: number, t: T, exists: (k: string) => boolean): string {
  for (const k of [`fix.${problem.code}.${f.type}`, f.id ? `fix.${f.id}` : "", f.target ? `fix.${f.target}` : ""]) {
    if (k && exists(k)) return t(k);
  }
  const cta = exists(`errors.${problem.code}.cta`) ? t(`errors.${problem.code}.cta`, problem.params ?? {}) : "";
  const items = cta ? cta.split(" · ") : [];
  // the column lists fixes in catalog order only when the counts match; otherwise a positional pick would mislabel
  if (items.length === (problem.fixes ?? []).length && items[i]?.trim()) return items[i].trim();
  return t(`fix.${f.type}`);
}

export function ProblemCard({ problem, onFix, tone = "warning" }: { problem: Problem; onFix?: (f: Fix) => void; tone?: "warning" | "danger" }) {
  const { t, i18n } = useTranslation();
  const params = problem.params ?? {};
  const title = t(`errors.${problem.code}.title`, { ...params, defaultValue: problem.code });
  return (
    <div role="group" aria-label={title} className="space-y-2 rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--surface)] p-4" data-code={problem.code}>
      <p className="font-semibold">
        <span aria-hidden="true" style={{ color: `var(--${tone})` }}>
          ⚠{" "}
        </span>
        {title}
      </p>
      <p className="text-sm text-[var(--text-muted)]">{t(`errors.${problem.code}.message`, { ...params, defaultValue: "" })}</p>
      <div className="flex flex-wrap gap-2">
        {(problem.fixes ?? []).map((f, i) => {
          const label = fixLabel(problem, f, i, t, (k) => i18n.exists(k));
          const cls = `rounded-[var(--radius-md)] px-3 py-1.5 text-sm ${i === 0 ? "bg-[var(--accent)] text-[var(--accent-fg)]" : "border border-[var(--border)]"}`;
          return f.type === "open_url" && f.url ? (
            <a key={i} href={f.url} target="_blank" rel="noreferrer" className={cls} onClick={() => onFix?.(f)}>
              {label}
            </a>
          ) : (
            <button key={i} type="button" className={cls} onClick={() => onFix?.(f)}>
              {label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
