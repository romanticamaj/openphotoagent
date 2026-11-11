// ★ and 「加入相簿」 in the Lightbox (ADR-0041): database sets, never files; the photo does not change.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
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
  return { on, toggle: () => flip.mutate(), ready: info.isSuccess };
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

export function AddToAlbum({ assetIds }: { assetIds: string[] }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [done, setDone] = useState<string | null>(null);
  const opener = useRef<HTMLButtonElement>(null);
  const list = useQuery({ queryKey: ["albums"], queryFn: api.albums, enabled: open });
  const add = useMutation({
    mutationFn: async (a: { id?: string; name: string }) => {
      const id = a.id ?? (await api.createAlbum(a.name)).id;
      await api.addToAlbum(id, assetIds);
      return a.name;
    },
    onSuccess: (n) => {
      setDone(t("albums.added", { name: n }));
      setOpen(false);
      setName("");
      opener.current?.focus(); // the field it was in is gone: keys stay with the Lightbox (Esc, ←, →)
      void qc.invalidateQueries({ queryKey: ["albums"] });
      for (const a of assetIds) void qc.invalidateQueries({ queryKey: ["asset", a] });
    },
  });
  return (
    <div className="relative">
      <button ref={opener} type="button" aria-haspopup="menu" aria-expanded={open} aria-label={t("albums.addTo")} title={t("albums.addTo")} onClick={() => setOpen((o) => !o)} className="icon-button">
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" aria-hidden="true">
          <path d="M4 7h11M4 12h11M4 17h7M18 14v6M15 17h6" />
        </svg>
      </button>
      {open ? (
        <div className="album-menu" role="menu" aria-label={t("albums.addTo")}>
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
      {done ? (
        <span role="status" className="toast" onAnimationEnd={() => setDone(null)}>
          {done}
        </span>
      ) : null}
    </div>
  );
}
