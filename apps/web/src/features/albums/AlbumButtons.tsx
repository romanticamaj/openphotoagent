// ★ and 「加入相簿」 in the Lightbox (ADR-0041): database sets, never files; the photo does not change.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../../lib/api";

/** Keys typed into a field are text, not shortcuts (COL-008): 「問這張」 lost the panel at every 「i」. */
export function typingIn(el: EventTarget | null): boolean {
  const tag = (el as HTMLElement | null)?.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || Boolean((el as HTMLElement | null)?.isContentEditable);
}

function Star({ on }: { on: boolean }) {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" fill={on ? "currentColor" : "none"} stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round">
      <path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z" />
    </svg>
  );
}

export function useFavorite(assetId: string) {
  const { i18n } = useTranslation();
  const qc = useQueryClient();
  const info = useQuery({ queryKey: ["asset", assetId, i18n.language], queryFn: () => api.asset(assetId, i18n.language) });
  const on = Boolean(info.data?.favorite);
  const flip = useMutation({
    mutationFn: () => api.setFavorite(assetId, !on),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: ["asset", assetId] });
      void qc.invalidateQueries({ queryKey: ["albums"] });
    },
  });
  // until this photo's state is in, F does nothing: right after → it toggled the photo before (audit round 33)
  return { on, toggle: () => info.isSuccess && !flip.isPending && flip.mutate(), ready: info.isSuccess };
}

export function FavoriteButton({ assetId }: { assetId: string }) {
  const { t } = useTranslation();
  const fav = useFavorite(assetId);
  if (!fav.ready) return null;
  const label = t(fav.on ? "albums.unfavorite" : "albums.favorite");
  return (
    <button type="button" aria-pressed={fav.on} aria-label={label} title={`${label} (F)`} onClick={fav.toggle} className={`icon-button ${fav.on ? "text-[var(--accent)]" : ""}`}>
      <Star on={fav.on} />
    </button>
  );
}

type Put = (albumId: string) => Promise<{ added?: string[] } | unknown>;
type Done = { text: string; albumId?: string; added?: string[] };

/** 「已加入…」 and, after a batch add, 復原: it takes out the photos this add put in, not those already there (COL-018). */
function AddedToast({ done, onGone, back }: { done: Done; onGone: () => void; back?: React.RefObject<HTMLElement | null> }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const undo = useMutation({
    // in pieces the API takes (500 a request): 841 photos in one went back 422 and nothing was undone (COL-022)
    mutationFn: async () => {
      const ids = done.added!;
      for (let i = 0; i < ids.length; i += UNDO_CHUNK) await api.removeManyFromAlbum(done.albumId!, ids.slice(i, i + UNDO_CHUNK));
    },
    onSuccess: () => {
      back?.current?.focus(); // 復原 goes away with the toast: focus back where the add began (COL-026)
      void qc.invalidateQueries({ queryKey: ["albums"] });
      void qc.invalidateQueries({ queryKey: ["album"] });
      void qc.invalidateQueries({ queryKey: ["asset"] });
    },
  });
  const canUndo = Boolean(done.albumId && done.added?.length);
  // undone: said so for a moment, then gone (COL-025: the toast just vanished and nobody knew whether it worked)
  useEffect(() => {
    if (!undo.isSuccess) return;
    const id = window.setTimeout(onGone, UNDONE_MS);
    return () => window.clearTimeout(id);
  }, [undo.isSuccess, onGone]);
  if (undo.isSuccess) {
    return (
      <span role="status" className="toast">
        {t("albums.undone", { n: (done.added?.length ?? 0).toLocaleString() })}
      </span>
    );
  }
  return (
    <span role="status" className="toast" onAnimationEnd={onGone}>
      {done.text}
      {canUndo ? (
        <button type="button" className="ml-2 underline" onClick={() => !undo.isPending && undo.mutate()}>
          {t("albums.undo")}
        </button>
      ) : null}
      {undo.isError ? (
        <span role="alert" className="ml-2 text-[var(--danger,#f87171)]">
          {t("albums.undoFailed")}
        </span>
      ) : null}
    </span>
  );
}

const addedOf = (r: unknown): string[] | undefined => (r as { added?: string[] } | null)?.added;

/** ★ for many photos at once: a selection goes into 收藏 in one request (COL-015). */
export function FavoriteSelection({ put }: { put: Put }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const starRef = useRef<HTMLButtonElement>(null);
  const [done, setDone] = useState<Done | null>(null);
  const fav = useMutation({
    mutationFn: () => put(FAVORITES),
    onSuccess: (r) => {
      setDone({ text: t("albums.added", { name: t("albums.favorites") }), albumId: FAVORITES, added: addedOf(r) });
      void qc.invalidateQueries({ queryKey: ["albums"] });
      void qc.invalidateQueries({ queryKey: ["album"] });
      void qc.invalidateQueries({ queryKey: ["asset"] });
    },
  });
  return (
    <span className="relative">
      <button ref={starRef} type="button" aria-label={t("albums.favorite")} title={t("albums.favorite")} aria-busy={fav.isPending} onClick={() => !fav.isPending && fav.mutate()} className="icon-button">
        <Star on={false} />
      </button>
      {done ? <AddedToast done={done} onGone={() => setDone(null)} back={starRef} /> : null}
    </span>
  );
}

const FAVORITES = "fav-default";
const UNDONE_MS = 2500; // how long 「已復原 N 張」 stays
const UNDO_CHUNK = 500; // the most photos /albums/{id}/photos/remove takes in one request
const MENU_W = 240; // .album-menu's width, near enough: min-width 220 px plus its padding

/** 「加入相簿」: one photo in the Lightbox (assetIds), or a whole selection (put) from the batch bar (COL-015). */
export function AddToAlbum({ assetIds, put }: { assetIds?: string[]; put?: Put }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [done, setDone] = useState<Done | null>(null);
  const opener = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const list = useQuery({ queryKey: ["albums"], queryFn: api.albums, enabled: open });
  const focused = useRef(false);
  // which way it opens: rightwards from the button where there is room (COL-019, COL-020)
  const [start, setStart] = useState(false);
  useEffect(() => {
    if (!open) {
      focused.current = false;
      return;
    }
    if (focused.current || !list.isSuccess) return;
    focused.current = true; // into the menu once it has its albums, so ↓ works at once
    requestAnimationFrame(() => menuRef.current?.querySelector<HTMLElement>('[role="menuitem"], input')?.focus());
  }, [open, list.isSuccess]);
  const items = () => [...(menuRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"], input') ?? [])];
  const add = useMutation({
    mutationFn: async (a: { id?: string; name: string }) => {
      const id = a.id ?? (await api.createAlbum(a.name)).id;
      const r = await (put ? put(id) : api.addToAlbum(id, assetIds ?? []));
      return { name: a.name, id, added: put ? addedOf(r) : undefined };
    },
    onSuccess: (n) => {
      setDone({ text: t("albums.added", { name: n.name }), albumId: n.id, added: n.added });
      setOpen(false);
      setName("");
      opener.current?.focus(); // the field it was in is gone: keys stay with the Lightbox (Esc, ←, →)
      void qc.invalidateQueries({ queryKey: ["albums"] });
      if (put) void qc.invalidateQueries({ queryKey: ["asset"] });
      for (const a of assetIds ?? []) void qc.invalidateQueries({ queryKey: ["asset", a] });
    },
  });
  return (
    <div
      className="relative"
      onKeyDown={(e) => {
        // the whole control, not only the menu: opened with the mouse, focus is still on its button (COL-011)
        if (e.key !== "Escape" || !open) return;
        e.stopPropagation(); // the menu closes, the Lightbox stays (COL-009)
        setOpen(false);
        opener.current?.focus();
      }}
    >
      <button ref={opener} type="button" aria-haspopup="menu" aria-expanded={open} aria-label={t("albums.addTo")} title={t("albums.addTo")} onClick={() => {
          // rightwards unless the window has no room there (COL-020): leftwards it went under the sidebar
          setStart((opener.current?.getBoundingClientRect().left ?? 0) + MENU_W <= window.innerWidth);
          setOpen((o) => !o);
        }} className="icon-button">
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" aria-hidden="true">
          <path d="M4 7h11M4 12h11M4 17h7M18 14v6M15 17h6" />
        </svg>
      </button>
      {open ? (
        <div
          ref={menuRef}
          className={start ? "album-menu album-menu-start" : "album-menu"}
          role="menu"
          aria-label={t("albums.addTo")}
          onKeyDown={(e) => {
            if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
            const all = items();
            if (!all.length) return;
            e.preventDefault();
            e.stopPropagation(); // the Lightbox's and the grid's arrows stay out of it
            const i = all.indexOf(document.activeElement as HTMLElement);
            all[(i + (e.key === "ArrowDown" ? 1 : -1) + all.length) % all.length].focus();
          }}
        >
          {(list.data?.albums ?? [])
            .filter((a) => a.kind === "album")
            .map((a) => (
              <button key={a.id} type="button" role="menuitem" onClick={() => add.mutate({ id: a.id, name: a.name })}>
                {a.name} <span className="tnum text-[var(--text-muted)]">{a.count.toLocaleString()}</span>
              </button>
            ))}
          <form
            className="flex gap-1 pt-1"
            onSubmit={(e) => {
              e.preventDefault();
              if (name.trim()) add.mutate({ name: name.trim() });
            }}
          >
            <input aria-label={t("albums.newName")} placeholder={t("albums.newName")} value={name} onChange={(e) => setName(e.target.value)} className="min-w-0 flex-1 rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--bg)] px-2 py-0.5 text-sm" />
            <button type="submit" className="whitespace-nowrap rounded-full bg-[var(--accent)] px-2 text-xs text-[var(--accent-fg)]">
              {t("albums.createAndAdd")}
            </button>
          </form>
        </div>
      ) : null}
      {done ? <AddedToast done={done} onGone={() => setDone(null)} back={opener} /> : null}
    </div>
  );
}
