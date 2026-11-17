// The memories a person hid and the subjects they want fewer of, at the end of 分類, each with 還原 (MEM-048): after a
// reload a hidden memory could never come back (audit round 140).
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "../../lib/api";

type Item = { id: string; kind: string; ref: string; name?: string | null };

function label(t: (k: string, o?: Record<string, unknown>) => string, x: Item): string {
  const p = x.ref.split(":");
  if (p[0] === "theme") return t("memories.themeTitle", { year: p[1], name: x.name ?? p[2] });
  if (p[0] === "event") {
    const [y, m, d] = (p[1] ?? "").split("-").map(Number);
    return t("memories.hiddenEvent", { day: `${y}/${m}/${d}` });
  }
  return t("memories.fewer", { name: x.name ?? p[1] });
}

export function HiddenMemories() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["memory-feedback"], queryFn: () => api.memoryFeedbackList() });
  const items = (q.data?.items ?? []) as Item[];
  if (!items.length) return null;
  return (
    <details className="hidden-memories text-sm">
      <summary className="cursor-pointer text-[var(--text-muted)]">{t("memories.hiddenList", { n: items.length })}</summary>
      <ul className="mt-2 space-y-1">
        {items.map((x) => (
          <li key={x.id} className="flex items-center gap-3">
            <span>{label(t, x)}</span>
            <button
              type="button"
              className="chip"
              aria-label={t("memories.restoreOne", { what: label(t, x) })}
              onClick={async () => {
                await api.memoryFeedbackRemove(x.id);
                for (const k of ["memory-feedback", "memory-themes", "memory-events"]) void qc.invalidateQueries({ queryKey: [k] });
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
