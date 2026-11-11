// Albums (ADR-0041): 「收藏」 first, then the albums, each a card with its newest photo; an album opens as a grid with the
// Lightbox. Albums are database sets: deleting one keeps its photos (COL-004).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, mediaUrl } from "../../lib/api";
import { Lightbox } from "../viewer/Lightbox";
import type { GridApi } from "../viewer/PhotoGrid";

export function AlbumsPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const nav = useNavigate();
  const list = useQuery({ queryKey: ["albums"], queryFn: api.albums });
  const [name, setName] = useState("");
  const create = useMutation({
    mutationFn: () => api.createAlbum(name.trim()),
    onSuccess: (a) => {
      setName("");
      void qc.invalidateQueries({ queryKey: ["albums"] });
      void nav({ to: "/albums/$albumId", params: { albumId: a.id } });
    },
  });
  const albums = list.data?.albums ?? [];
  return (
    <section className="space-y-4 p-4" aria-labelledby="albums-h">
      <div className="flex flex-wrap items-center gap-3">
        <h1 id="albums-h" className="text-xl font-semibold">{t("albums.title")}</h1>
        <form
          className="ml-auto flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (name.trim()) create.mutate();
          }}
        >
          <input aria-label={t("albums.newName")} placeholder={t("albums.newName")} value={name} onChange={(e) => setName(e.target.value)} className="rounded-full border border-[var(--border)] bg-[var(--bg)] px-3 py-1 text-sm" />
          <button type="submit" className="rounded-full bg-[var(--accent)] px-3 py-1 text-sm text-[var(--accent-fg)]">{t("albums.create")}</button>
        </form>
      </div>
      {list.isSuccess && albums.length <= 1 && !albums[0]?.count ? <p className="text-sm text-[var(--text-muted)]">{t("albums.empty")}</p> : null}
      <ul className="album-grid" data-testid="album-list">
        {albums.map((a) => (
          <li key={a.id}>
            <Link to="/albums/$albumId" params={{ albumId: a.id }} className="album-card">
              <div className="album-cover">
                {a.cover ? <img src={mediaUrl(a.cover.asset_id, "t512", a.cover.ch)} alt="" loading="lazy" /> : <span aria-hidden="true">{a.kind === "favorites" ? "★" : "▢"}</span>}
              </div>
              <p className="font-medium">{a.kind === "favorites" ? `★ ${t("albums.favorites")}` : a.name}</p>
              <p className="tnum text-xs text-[var(--text-muted)]">{t("albums.count", { n: a.count.toLocaleString() })}</p>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function AlbumPage({ albumId }: { albumId: string }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const nav = useNavigate();
  const q = useQuery({ queryKey: ["album", albumId], queryFn: () => api.album(albumId) });
  const [viewing, setViewing] = useState<number | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);
  const photos = useMemo(() => q.data?.photos ?? [], [q.data]);
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["album", albumId] });
    void qc.invalidateQueries({ queryKey: ["albums"] });
  };
  const remove = useMutation({ mutationFn: (assetId: string) => api.removeFromAlbum(albumId, assetId), onSuccess: refresh });
  const rename = useMutation({ mutationFn: (n: string) => api.renameAlbum(albumId, n), onSuccess: () => (setRenaming(null), refresh()) });
  const drop = useMutation({
    mutationFn: () => api.deleteAlbum(albumId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["albums"] });
      void nav({ to: "/albums" });
    },
  });
  const grid: GridApi = useMemo(
    () => ({
      total: photos.length,
      ordinal: (p) => p.index,
      at: async (i) => {
        const p = photos[i];
        if (!p) return null;
        return { asset: { id: p.asset_id, ch: p.ch, w: null, h: null, thumbhash: null, taken_at: p.taken_at, flags: [] }, pos: { bucket: 0, index: i } };
      },
    }),
    [photos],
  );
  const fav = q.data?.kind === "favorites";
  return (
    <section className="space-y-4 p-4" aria-labelledby="album-h">
      <Link to="/albums" className="text-sm text-[var(--text-muted)] underline">{t("albums.back")}</Link>
      <div className="flex flex-wrap items-center gap-3">
        {renaming !== null ? (
          <form
            className="flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (renaming.trim()) rename.mutate(renaming.trim());
            }}
          >
            <input aria-label={t("albums.rename")} value={renaming} onChange={(e) => setRenaming(e.target.value)} className="rounded-full border border-[var(--border)] bg-[var(--bg)] px-3 py-1" />
            <button type="submit" className="rounded-full bg-[var(--accent)] px-3 py-1 text-sm text-[var(--accent-fg)]">{t("albums.save")}</button>
          </form>
        ) : (
          <h1 id="album-h" className="text-xl font-semibold">{fav ? `★ ${t("albums.favorites")}` : q.data?.name}</h1>
        )}
        <span className="tnum text-[var(--text-muted)]">{t("albums.count", { n: photos.length.toLocaleString() })}</span>
        {!fav && q.data ? (
          <span className="ml-auto flex gap-3 text-sm">
            <button type="button" className="underline" onClick={() => setRenaming(q.data.name)}>{t("albums.rename")}</button>
            <button type="button" className="underline" title={t("albums.deleteNote")} onClick={() => drop.mutate()}>{t("albums.delete")}</button>
          </span>
        ) : null}
      </div>
      {q.isSuccess && photos.length === 0 ? <p className="text-sm text-[var(--text-muted)]">{t("albums.empty")}</p> : null}
      <ul className="grid grid-cols-[repeat(auto-fill,minmax(160px,1fr))] gap-0.5" aria-label={q.data?.name ?? ""}>
        {photos.map((p, i) => (
          <li key={p.asset_id} className="tile relative aspect-square">
            <button type="button" className="h-full w-full" onClick={() => setViewing(i)} aria-label={t("albums.open")}>
              <img src={mediaUrl(p.asset_id, "t256", p.ch)} alt="" loading="lazy" className="h-full w-full object-cover" />
            </button>
            <button type="button" className="album-remove" aria-label={t("albums.remove")} title={t("albums.remove")} onClick={() => remove.mutate(p.asset_id)}>
              ×
            </button>
          </li>
        ))}
      </ul>
      {viewing !== null ? <Lightbox start={viewing} grid={grid} onClose={() => (setViewing(null), refresh())} /> : null}
    </section>
  );
}
