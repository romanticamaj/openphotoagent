// The folders already in the library, each with its photos and how many the AI has seen (IMP-033). 匯入 analyses them
// again from here; 整理 starts from them, so the two pages no longer open on the same folder tree.
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, type LibrarySource } from "../lib/api";

export function LibraryFolders({ action, onAct, busy }: { action: (s: LibrarySource) => string; onAct: (s: LibrarySource) => void; busy?: string | null }) {
  const { t, i18n } = useTranslation();
  const q = useQuery({ queryKey: ["sources"], queryFn: api.sources, refetchInterval: 5000 });
  // a folder inside another folder of the library is covered by it: listing both counted its photos twice (IMP-034)
  const all = q.data ?? [];
  const inside = (a: LibrarySource, b: LibrarySource) =>
    a.id !== b.id && a.root_alias === b.root_alias && (b.sub_path === "" || a.sub_path.startsWith(`${b.sub_path}/`));
  const rows = all.filter((s) => !all.some((o) => inside(s, o)));
  if (!rows.length) return null;
  const num = (n: number) => n.toLocaleString(i18n.language);
  return (
    <ul className="library-folders" aria-label={t("library.inLibrary")}>
      {rows.map((s) => {
        const name = s.sub_path ? s.sub_path.split("/").at(-1)! : s.root_alias;
        const pct = s.photos ? Math.round((100 * s.analysed) / s.photos) : 0;
        return (
          <li key={s.id}>
            <div className="min-w-0">
              <p className="break-words font-medium">{name}</p>
              <p className="break-words text-xs text-[var(--text-muted)]">{s.sub_path ? `${s.root_alias} / ${s.sub_path}` : t("library.wholeRoot")}</p>
              <p className="tnum text-xs text-[var(--text-muted)]">{t("library.folderCounts", { photos: num(s.photos), analysed: num(s.analysed) })}</p>
              <div role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct} aria-label={t("library.analysedShare")} className="mt-1 h-1 w-full rounded bg-[var(--surface-2)]">
                <div className="h-full rounded bg-[var(--accent)]" style={{ width: `${pct}%` }} />
              </div>
            </div>
            <button type="button" className="library-folder-action" onClick={() => onAct(s)} aria-busy={busy === s.id}>
              {action(s)}
            </button>
          </li>
        );
      })}
    </ul>
  );
}
