import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../lib/api";
import { Button } from "./Button";

/** Browse inside the roots the user registered in the app (invariant 2: no free-form paths). */
export function FolderPicker({ onPick, title }: { onPick: (root: string, path: string) => void; title?: string }) {
  const { t } = useTranslation();
  const [root, setRoot] = useState<string | null>(null);
  const [path, setPath] = useState<string[]>([]);
  const roots = useQuery({ queryKey: ["roots"], queryFn: api.roots });
  const dirs = useQuery({
    queryKey: ["browse", root, path.join("/")],
    queryFn: () => api.browse(root!, path.join("/")),
    enabled: root !== null,
  });
  return (
    <section aria-labelledby="pick-title" className="space-y-3">
      <h2 id="pick-title" className="text-lg font-semibold">{title ?? t("organize.pickFolder")}</h2>
      <div className="flex flex-wrap gap-2">
        {(roots.data ?? []).map((r) => (
          <Button key={r.alias} variant="ghost" onClick={() => { setRoot(r.alias); setPath([]); }}>{r.alias}</Button>
        ))}
      </div>
      {root && (
        <div className="space-y-2">
          <p className="text-sm text-[var(--text-muted)]" data-testid="current-path">{[root, ...path].join(" / ")}</p>
          <div className="flex flex-wrap gap-2">
            {(dirs.data?.dirs ?? []).map((d) => (
              <Button key={d} variant="ghost" onClick={() => setPath([...path, d])}>{d}</Button>
            ))}
          </div>
          <Button onClick={() => onPick(root, path.join("/"))}>{t("organize.useThisFolder")}</Button>
        </div>
      )}
    </section>
  );
}
