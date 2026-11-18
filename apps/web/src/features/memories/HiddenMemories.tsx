// The memories a person hid and the subjects they want fewer of, at the end of 分類, each with 還原 (MEM-048): after a
// reload a hidden memory could never come back (audit round 140).
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../../lib/api";
import { eventTitle, themeTitle } from "./titles";

type Item = { id: string; kind: string; ref: string; name?: string | null };

/** Named as its card is (round 143, MEM-059): 「去年的球」 there was 「2025 的球」 here. */
function label(t: (k: string, o?: Record<string, unknown>) => string, x: Item, today?: string): string {
  const p = x.ref.split(":");
  if (p[0] === "theme") return themeTitle(t, Number(p[1]), x.name ?? p[2] ?? "", today);
  if (p[0] === "event") return t("memories.hiddenEvent", { day: eventTitle(t, p[1] ?? "", null, null, today) });
  return t("memories.fewer", { name: x.name ?? p[1] });
}

export function HiddenMemories() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["memory-feedback"], queryFn: () => api.memoryFeedbackList() });
  const items = (q.data?.items ?? []) as Item[];
  const summary = useRef<HTMLElement>(null);
  if (!items.length) return null;
  return (
    <details className="hidden-memories text-sm">
      <summary ref={summary} className="cursor-pointer text-[var(--text-muted)]">{t("memories.hiddenList", { n: items.length })}</summary>
      <ul className="mt-2 space-y-1">
        {items.map((x) => (
          <li key={x.id} className="flex items-center gap-3">
            <span>{label(t, x, q.data?.today)}</span>
            <button
              type="button"
              className="chip"
              aria-label={t("memories.restoreOne", { what: label(t, x, q.data?.today) })}
              onClick={async () => {
                await api.memoryFeedbackRemove(x.id);
                const last = items.length === 1;
                await Promise.all(["memory-feedback", "memory-themes", "memory-events"].map((k) => qc.invalidateQueries({ queryKey: [k] })));
                // its row is gone: the list's heading (MEM-053); the last one takes the list too, so the themes'
                // heading (round 142, MEM-057)
                const heading = last ? document.getElementById("themes-h") : null;
                (heading ?? summary.current)?.focus();
              }}
            >
              {t("memories.restore")}
            </button>
          </li>
        ))}
      </ul>
    </details>
  );
}
