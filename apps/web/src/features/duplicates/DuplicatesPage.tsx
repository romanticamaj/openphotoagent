// Duplicate groups (05 §2.2, 10 §4.2): exact copies and near duplicates, the copy we keep, and a one-click
// clean-up plan that moves the others to their folder's trash — reviewed and approved like any plan (TRASH-005).
import { useMutation, useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "../../components/Button";
import { api, mediaUrl, type DuplicateGroup } from "../../lib/api";
import { formatBytes } from "../../lib/format";

function dropCount(g: DuplicateGroup): number {
  return g.members.length - 1;
}

export function DuplicatesPage() {
  const { t, i18n } = useTranslation();
  const nav = useNavigate();
  const q = useQuery({ queryKey: ["duplicates"], queryFn: api.duplicates });
  const groups = useMemo(() => q.data?.groups ?? [], [q.data]);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const all = groups.length > 0 && picked.size === groups.length;
  const count = groups.filter((g) => picked.has(g.group_id)).reduce((n, g) => n + dropCount(g), 0);
  const make = useMutation({
    mutationFn: () => api.cleanupPlan([...picked]),
    onSuccess: (r) => void nav({ to: "/plans/$planId", params: { planId: r.plan_id } }),
  });
  if (q.isLoading) return <p role="status">{t("common.working")}</p>;
  return (
    <section className="mx-auto max-w-5xl space-y-4 p-8">
      <h1 className="text-xl font-semibold">{t("duplicates.title")}</h1>
      <p className="text-sm text-[var(--text-muted)]">{t("duplicates.explain")}</p>
      {groups.length === 0 ? (
        <p>{t("duplicates.empty")}</p>
      ) : (
        <>
          <div className="flex items-center gap-4">
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={all} onChange={(e) => setPicked(e.target.checked ? new Set(groups.map((g) => g.group_id)) : new Set())} />
              {t("duplicates.selectAll")}
            </label>
            <span className="text-sm text-[var(--text-muted)]">{t("duplicates.groups", { count: groups.length })}</span>
            <Button
              disabledReason={
                picked.size === 0 ? { code: "NOTHING_SELECTED", message: t("duplicates.selectAll") } : make.isPending ? { code: "BUSY", message: t("common.working") } : null
              }
              onClick={() => make.mutate()}
            >
              {t("duplicates.makePlan", { count })}
            </Button>
          </div>
          <ul className="grid grid-cols-1 gap-3 md:grid-cols-2">
            {groups.map((g) => (
              <li key={g.group_id} data-testid="dup-group" className="space-y-2 rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--surface)] p-3">
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={picked.has(g.group_id)}
                    onChange={(e) => {
                      const n = new Set(picked);
                      if (e.target.checked) n.add(g.group_id);
                      else n.delete(g.group_id);
                      setPicked(n);
                    }}
                  />
                  <span>{t(g.reason === "hash" ? "duplicates.exact" : "duplicates.near")}</span>
                  <span className="tnum text-[var(--text-muted)]">{t("duplicates.reclaimable", { size: formatBytes(g.reclaimable_bytes, i18n.language) })}</span>
                </label>
                <div className="flex gap-2">
                  {g.members.map((m, i) => (
                    <figure key={`${m.asset_id}-${i}`} className="w-24 space-y-1">
                      <img src={mediaUrl(m.asset_id, "t256", m.ch)} alt="" className="aspect-square w-full rounded-[var(--radius-photo)] object-cover" />
                      <figcaption className="truncate text-xs" title={m.rel_path ?? ""}>
                        {(g.reason === "hash" ? i === 0 : m.asset_id === g.representative.asset_id) ? t("duplicates.representative") : m.rel_path}
                      </figcaption>
                    </figure>
                  ))}
                </div>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
