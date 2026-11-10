// While a preview runs (ORG-013, invariant 9): how many samples are done, the time so far and a rough time left,
// then the library estimate; nothing once the estimate is in.
import { useTranslation } from "react-i18next";

export type PreviewProgressData = { done: number; total: number; elapsed_s: number };

function secs(t: (k: string, o?: Record<string, unknown>) => string, s: number): string {
  const n = Math.max(1, Math.round(s));
  return n < 60 ? t("organize.secs", { n }) : t("organize.mins", { m: Math.floor(n / 60), s: n % 60 });
}

export function PreviewProgress({ status, progress, hasEstimate }: { status: string; progress?: PreviewProgressData; hasEstimate: boolean }) {
  const { t } = useTranslation();
  if (status === "ready" && hasEstimate) return null;
  if (status === "ready" || !progress) {
    return (
      <p role="status" className="text-sm text-[var(--text-muted)]">
        {t("organize.estimating")}
      </p>
    );
  }
  if (progress.total === 0) {
    return (
      <p role="status" className="text-sm text-[var(--text-muted)]">
        {t("organize.readingFolder", { elapsed: secs(t, progress.elapsed_s) })}
      </p>
    );
  }
  const left = progress.done > 0 ? (progress.elapsed_s / progress.done) * (progress.total - progress.done) : null;
  return (
    <p role="status" className="text-sm text-[var(--text-muted)]">
      {t("organize.samplingProgress", { done: progress.done, total: progress.total, elapsed: secs(t, progress.elapsed_s) })}
      {left !== null && progress.done < progress.total ? ` · ${t("organize.timeLeft", { left: secs(t, left) })}` : ""}
    </p>
  );
}
