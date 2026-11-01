import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { formatDuration } from "../../lib/format";
import { ElapsedClock } from "../../lib/operations";
import type { Op } from "./model";

export type Variant = "inline" | "compact" | "card" | "full";

/** 06 §6.2: progress + ETA state + elapsed time, accumulated locally between events (ACT-007). */
export function OperationProgress({ op, variant = "compact" }: { op: Op; variant?: Variant }) {
  const { t, i18n } = useTranslation();
  const running = op.status === "running";
  const clock = useMemo(() => new ElapsedClock(), []);
  // re-sync only when the server sends new state; between events the clock runs locally (ACT-007)
  useMemo(
    () =>
      clock.sync({
        serverTime: op.server_now_ms ?? Date.now(),
        localNow: Date.now(),
        activeMs: op.active_ms ?? 0,
        updatedAt: op.updated_at_ms ?? Date.now(),
        running,
      }),
    [clock, op.server_now_ms, op.active_ms, op.updated_at_ms, running],
  );
  const [, tick] = useState(0);
  useEffect(() => {
    if (!running) return;
    const h = setInterval(() => tick((x) => x + 1), 1000);
    return () => clearInterval(h);
  }, [running]);
  const elapsed = clock.elapsedMs(Date.now());
  const pct = Math.round((op.fraction ?? 0) * 100);
  const eta = op.eta?.state;
  return (
    <div className={`op-progress op-${variant}`} data-status={op.status}>
      <div
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
        aria-label={t(`activity.type.${op.type}`, { defaultValue: op.type })}
        className="h-1.5 w-full rounded bg-[var(--surface-2)]"
      >
        <div className="h-full rounded bg-[var(--accent)]" style={{ width: `${pct}%` }} />
      </div>
      <div className="flex justify-between text-xs tabular-nums text-[var(--text-muted)]">
        <span>{t(`activity.status.${op.status}`, { defaultValue: op.status })}</span>
        {eta ? <span data-eta={eta}>{t(`activity.eta.${eta}`, { defaultValue: eta })}</span> : null}
        <span data-testid="elapsed">{formatDuration(elapsed, i18n.language)}</span>
      </div>
    </div>
  );
}
