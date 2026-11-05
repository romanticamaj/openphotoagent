import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../lib/api";
import { platform } from "../lib/platform";
import { Button } from "./Button";

/** Browse inside the roots the user registered in the app (invariant 2: no free-form paths). */
export function FolderPicker({ onPick, title }: { onPick: (root: string, path: string) => void; title?: string }) {
  const { t } = useTranslation();
  const [root, setRoot] = useState<string | null>(null);
  const [path, setPath] = useState<string[]>([]);
  const [awaited, setAwaited] = useState<string | null>(null);
  const roots = useQuery({
    queryKey: ["roots"],
    queryFn: api.roots,
    // the worker restarts to take a new root and registers it a moment later
    refetchInterval: (q) => (awaited && !q.state.data?.some((r) => r.alias === awaited) ? 500 : false),
  });
  const addRoot = platform().addRoot;
  const add = useMutation({
    mutationFn: () => addRoot!("ro"),
    onSuccess: (alias) => {
      if (alias) {
        setAwaited(alias);
        void roots.refetch();
      }
    },
  });
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
        {addRoot && (
          <Button data-testid="add-root" onClick={() => add.mutate()}>
            {t("organize.addFolder")}
          </Button>
        )}
      </div>
      {root && (
        <div className="space-y-2">
          <p className="text-sm text-[var(--text-muted)]" data-testid="current-path">{[root, ...path].join(" / ")}</p>
          <div className="flex flex-wrap gap-2">
            {(dirs.data?.dirs ?? []).map((d) => (
              <Button key={d} variant="ghost" onClick={() => setPath([...path, d])}>{d}</Button>
            ))}
          </div>
          <Button data-testid="use-folder" onClick={() => onPick(root, path.join("/"))}>
            {t("organize.useThisFolder")}
          </Button>
        </div>
      )}
    </section>
  );
}
