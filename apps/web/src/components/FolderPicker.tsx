import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api, type FolderPreview as Preview, type Root } from "../lib/api";
import { formatBytes } from "../lib/format";
import { platform } from "../lib/platform";
import { Button } from "./Button";

type Picked = { root: string; path: string };
const previewKey = (p: Picked) => ["folder-preview", p.root, p.path];
const previewOf = (p: Picked) => () => api.folderPreview(p.root, p.path);

/** Browse inside the roots the user registered in the app (invariant 2: no free-form paths), and see what a folder
 *  holds before committing to it (ADR-0029). */
export function FolderPicker({ onPick, title, action }: { onPick: (root: string, path: string) => void; title?: string; action?: string }) {
  const { t } = useTranslation();
  const [picked, setPicked] = useState<Picked | null>(null);
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
  return (
    <section aria-labelledby="pick-title" className="space-y-4">
      <h2 id="pick-title" className="text-lg font-semibold">{title ?? t("organize.pickFolder")}</h2>
      <div className="folder-browser">
        <div className="folder-tree">
          <ul>
            {(roots.data ?? []).map((r) => (
              <RootNode key={r.alias} root={r} picked={picked} onSelect={setPicked} />
            ))}
          </ul>
          {addRoot && (
            <Button data-testid="add-root" variant="ghost" onClick={() => add.mutate()}>
              {t("organize.addFolder")}
            </Button>
          )}
        </div>
        {picked ? <PreviewPane picked={picked} action={action ?? t("organize.useThisFolder")} onUse={() => onPick(picked.root, picked.path)} /> : <p className="folder-hint">{t("library.pickHint")}</p>}
      </div>
    </section>
  );
}

function RootNode({ root, picked, onSelect }: { root: Root; picked: Picked | null; onSelect: (p: Picked) => void }) {
  const { t } = useTranslation();
  return (
    <TreeNode
      at={{ root: root.alias, path: "" }}
      name={root.alias}
      note={root.mode === "rw" ? t("library.outputFolder") : undefined}
      picked={picked}
      onSelect={onSelect}
    />
  );
}

function TreeNode({ at, name, count, note, picked, onSelect }: { at: Picked; name: string; count?: number; note?: string; picked: Picked | null; onSelect: (p: Picked) => void }) {
  const { i18n } = useTranslation();
  const selected = picked?.root === at.root && picked.path === at.path;
  const open = picked?.root === at.root && (at.path === "" || picked.path === at.path || picked.path.startsWith(`${at.path}/`));
  const dirs = useQuery({ queryKey: ["browse", at.root, at.path], queryFn: () => api.browse(at.root, at.path), enabled: open });
  const preview = useQuery({ queryKey: previewKey(at), queryFn: previewOf(at), enabled: open, retry: false, staleTime: 30_000 });
  const counts = new Map((preview.data?.subfolders ?? []).map((s) => [s.name, s.photos + s.videos]));
  return (
    <li>
      <button type="button" aria-label={name} aria-current={selected || undefined} onClick={() => onSelect(at)} className="folder-row">
        <span aria-hidden="true" className="folder-caret">{open ? "▾" : "▸"}</span>
        <span className="truncate">{name}</span>
        {note ? <span className="folder-note">{note}</span> : null}
        {count !== undefined ? <span className="tnum folder-note">{count.toLocaleString(i18n.language)}</span> : null}
      </button>
      {open && (dirs.data?.dirs.length ?? 0) > 0 ? (
        <ul>
          {dirs.data!.dirs.map((d) => (
            <TreeNode key={d} at={{ root: at.root, path: at.path ? `${at.path}/${d}` : d }} name={d} count={counts.get(d)} picked={picked} onSelect={onSelect} />
          ))}
        </ul>
      ) : null}
    </li>
  );
}

function PreviewPane({ picked, action, onUse }: { picked: Picked; action: string; onUse: () => void }) {
  const { t, i18n } = useTranslation();
  const q = useQuery({ queryKey: previewKey(picked), queryFn: previewOf(picked), retry: false, staleTime: 30_000 });
  const p = q.data;
  const empty = p ? p.photos + p.videos === 0 : false;
  const crumbs = [picked.root, ...picked.path.split("/").filter(Boolean)];
  return (
    <div data-testid="folder-preview" className="folder-preview">
      <div>
        <p className="text-base font-semibold">{crumbs[crumbs.length - 1]}</p>
        <p className="text-xs text-[var(--text-muted)]" data-testid="current-path">{crumbs.join(" / ")}</p>
      </div>
      {q.isPending ? <p role="status" className="text-[var(--text-muted)]">{t("library.counting")}</p> : null}
      {q.isError ? <p role="status" className="text-[var(--text-muted)]">{t("library.previewFailed")}</p> : null}
      {p && !empty ? <Summary p={p} locale={i18n.language} /> : null}
      {p && !empty ? <Samples root={picked.root} rels={p.samples} /> : null}
      {p && empty ? <p role="status">{p.subfolders.length ? t("library.emptyTryChild") : t("library.empty")}</p> : null}
      {p && !empty ? (
        <p className="text-xs text-[var(--text-muted)]">
          {p.others > 0 ? `${t("library.skips", { n: p.others.toLocaleString(i18n.language) })} ` : ""}
          {p.truncated ? `${t("library.truncated")} ` : ""}
          {t("library.safe")}
        </p>
      ) : null}
      <div>
        <Button data-testid="use-folder" disabledReason={empty ? { code: "FOLDER_EMPTY", message: t("library.empty") } : null} onClick={onUse}>
          {action}
        </Button>
      </div>
    </div>
  );
}

function Summary({ p, locale }: { p: Preview; locale: string }) {
  const { t } = useTranslation();
  const day = (s: string) => new Date(s).toLocaleDateString(locale, { year: "numeric", month: "short", day: "numeric" });
  const stats: Array<[string, string]> = [
    [p.photos.toLocaleString(locale), t("library.photos")],
    ...(p.videos ? ([[p.videos.toLocaleString(locale), t("library.videos")]] as Array<[string, string]>) : []),
    [formatBytes(p.bytes, locale), t("library.size")],
  ];
  return (
    <>
      <dl className="folder-stats">
        {stats.map(([v, label]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd className="tnum">{v}</dd>
          </div>
        ))}
        {p.taken_from && p.taken_to ? (
          <div>
            <dt>{t("library.dates")}</dt>
            <dd className="tnum">{p.taken_from.slice(0, 10) === p.taken_to.slice(0, 10) ? day(p.taken_from) : `${day(p.taken_from)} – ${day(p.taken_to)}`}</dd>
          </div>
        ) : null}
      </dl>
    </>
  );
}

function Samples({ root, rels }: { root: string; rels: string[] }) {
  return (
    <div className="folder-samples">
      {rels.map((r) => (
        <SampleThumb key={r} root={root} rel={r} />
      ))}
    </div>
  );
}

function SampleThumb({ root, rel }: { root: string; rel: string }) {
  const { t } = useTranslation();
  const q = useQuery({ queryKey: ["folder-thumb", root, rel], queryFn: () => api.folderThumb(root, rel), retry: false, staleTime: Infinity });
  if (!q.data) return <div className="folder-sample" aria-hidden="true" />;
  return <img className="folder-sample" alt={t("library.sampleAlt")} src={`data:image/jpeg;base64,${q.data.thumbnail_b64}`} />;
}
