// 「移到垃圾桶」 (TRASH-013; the user approved it on 2026-11-12): the chosen photos become a plan you approve on the
// plan page; their files then go to their own folder's .opagent-trash/ and 復原 brings them back (invariant 3).
// Nothing moves from here. A read-only folder is said up front when every folder is read-only (TRASH-016), and a
// refusal says why, that nothing changed, and what to do instead, beside the button (TRASH-015, invariant 8).
import { useMutation, useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiError, api } from "../lib/api";

type Refusal = { code?: string; roots?: string[]; detail?: { code?: string; roots?: string[] } };

export function TrashButton({ select, compact = false }: { select: () => Promise<{ id: string }>; compact?: boolean }) {
  const { t } = useTranslation();
  const nav = useNavigate();
  const uid = useId().replace(/:/g, "");
  const roots = useQuery({ queryKey: ["roots"], queryFn: api.roots });
  const sources = useQuery({ queryKey: ["sources"], queryFn: api.sources });
  // the library's own folders, not every root: the output folder 「sorted」 is writable while every photo sits in a
  // read-only one, and the button stayed enabled on a library where it could never work (audit round 62)
  const mode = new Map((roots.data ?? []).map((r) => [r.alias, r.mode]));
  const libraryRoots = [...new Set((sources.data ?? []).map((x) => x.root_alias))];
  const checked = libraryRoots.length ? libraryRoots : [...mode.keys()];
  const readOnly = roots.isSuccess && checked.length > 0 && checked.every((a) => mode.get(a) !== "rw");
  const [shown, setShown] = useState<string[] | null>(null); // the folders a refusal names, while its card is open
  const go = useMutation({
    mutationFn: async () => api.selectionTrash((await select()).id),
    onSuccess: (r) => void nav({ to: "/plans/$planId", params: { planId: r.plan_id } }),
    onError: (e) => {
      const body = e instanceof ApiError ? ((e.body ?? {}) as Refusal) : null;
      const refusal = body ? (body.detail ?? body) : null;
      setShown(refusal?.code === "FS_NOT_WRITABLE" ? (refusal.roots ?? []) : []);
    },
  });
  const why = readOnly ? t("trash.allReadOnly") : null;
  const press = () => {
    if (readOnly) return setShown(checked); // said before any request (TRASH-016)
    if (!go.isPending) go.mutate();
  };
  const self = useRef<HTMLButtonElement>(null);
  // closing the card gives the keys back to the button: focus fell to the page and the Lightbox stopped hearing
  // ←, → and Esc (TRASH-017)
  const close = () => {
    setShown(null);
    self.current?.focus();
  };
  // the card takes focus as it opens, so its Esc is its own (TRASH-018)
  const closer = useRef<HTMLButtonElement>(null);
  const open = shown !== null;
  useEffect(() => {
    if (open) closer.current?.focus();
  }, [open]);
  return (
    <span
      className="relative inline-flex flex-col"
      onKeyDown={(e) => {
        // the whole control, as the album menu does: with focus still on the button, Esc closed the card and also the
        // Lightbox, or cleared the selection (audit round 63)
        if (e.key !== "Escape" || !open) return;
        e.stopPropagation();
        e.preventDefault();
        close();
      }}
    >
      <button
        ref={self}
        type="button"
        data-trash=""
        aria-label={t("trash.move")}
        title={why ?? t("trash.move")}
        aria-busy={go.isPending}
        aria-disabled={readOnly || undefined}
        aria-describedby={why && !compact ? `why-${uid}` : undefined}
        aria-description={why && compact ? why : undefined}
        onClick={press}
        className={`${compact ? "icon-button" : "rounded-[var(--radius-sm)] border border-[var(--border)] px-3 py-1 text-sm hover:bg-[var(--surface-2)]"} aria-disabled:opacity-50`}
      >
        {compact ? (
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v6M14 11v6" />
          </svg>
        ) : (
          t("trash.move")
        )}
      </button>
      {why && !compact ? (
        // shown beside the text button; the Lightbox's icon carries it as its description and tooltip (invariant 8)
        <span id={`why-${uid}`} className="text-xs text-[var(--text-muted)]">
          {why}
        </span>
      ) : null}
      {shown !== null ? (
        // floats beside the button: the selection bar keeps its height, the Lightbox does not clip it (TRASH-015)
        <span className={compact ? "trash-popover trash-popover-end" : "trash-popover"}>
          <span role="alert" className="space-y-1">
            <strong className="block">
              {shown.length ? t("trash.readOnly", { roots: shown.map((r) => `「${r}」`).join("、") }) : t("trash.failed")}
            </strong>
            <span className="block text-sm">{t("trash.nothingMoved")}</span>
            {shown.length ? (
              <span className="block text-sm">
                <Link to="/import" className="underline">
                  {t("trash.reAdd")}
                </Link>{" "}
                {t("trash.orCopy")}
              </span>
            ) : null}
          </span>
          <button ref={closer} type="button" className="trash-popover-close" aria-label={t("trash.close")} onClick={close}>
            ×
          </button>
        </span>
      ) : null}
    </span>
  );
}
