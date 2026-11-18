// A memory card's 「⋯」 (ADR-0049 addendum 8, MEM-042): 隱藏這個回憶, and 少一點「X」 for a card named by a subject. Kept
// per person on the server (memory_feedback); 復原 in the toast brings it back.
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../../lib/api";

type Done = { id: string; text: string } | null;

/** The section's toast, kept above the cards: a hidden card is gone before its toast could show. */
export function useMemoryFeedback(headingId?: string) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [done, setDone] = useState<Done>(null);
  // the card is gone: the keyboard goes to 復原, not the page (round 140, MEM-047)
  const undoRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (done) undoRef.current?.focus();
  }, [done]);
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["memory-themes"] });
    void qc.invalidateQueries({ queryKey: ["memory-events"] });
    void qc.invalidateQueries({ queryKey: ["memory-feedback"] });
  };
  const send = async (kind: "hide" | "fewer", ref: string, text: string) => {
    const r = await api.memoryFeedback(kind, ref);
    setDone({ id: r.id, text });
    refresh();
  };
  const toast = done ? (
    <p role="status" className="toast flex items-center gap-2 text-sm">
      <span>{done.text}</span>
      <button
        ref={undoRef}
        type="button"
        className="chip"
        onClick={async () => {
          await api.memoryFeedbackRemove(done.id);
          setDone(null);
          refresh();
          // the toast goes: the keyboard to the section's heading, not the page (round 141, MEM-053)
          if (headingId) document.getElementById(headingId)?.focus();
        }}
      >
        {t("memories.undo")}
      </button>
      <button type="button" className="underline" aria-label={t("share.dismiss")} onClick={() => setDone(null)}>
        ×
      </button>
    </p>
  ) : null;
  return { send, toast };
}

export function MemoryMenu({ title, hideRef, fewer, send }: { title: string; hideRef: string; fewer?: { key: string; name: string }; send: (kind: "hide" | "fewer", ref: string, text: string) => Promise<void> }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const btn = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (open) menu.current?.querySelector<HTMLElement>("[role=menuitem]")?.focus();
  }, [open]);
  const close = () => {
    setOpen(false);
    btn.current?.focus();
  };
  return (
    <span className="relative">
      <button ref={btn} type="button" className="memory-more" aria-haspopup="menu" aria-expanded={open} aria-label={t("memories.moreFor", { title })} onClick={() => setOpen((o) => !o)}>
        <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" aria-hidden="true">
          <circle cx="5" cy="12" r="2" />
          <circle cx="12" cy="12" r="2" />
          <circle cx="19" cy="12" r="2" />
        </svg>
      </button>
      {open ? (
        <div
          ref={menu}
          role="menu"
          className="memory-menu"
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              e.stopPropagation();
              close();
              return;
            }
            // ↓ ↑ between the items, round (MEM-047)
            if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
            e.preventDefault();
            const items = [...(menu.current?.querySelectorAll<HTMLElement>("[role=menuitem]") ?? [])];
            const at = items.indexOf(document.activeElement as HTMLElement);
            items[(at + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length]?.focus();
          }}
          onBlur={(e) => {
            if (!e.currentTarget.contains(e.relatedTarget as Node | null) && e.relatedTarget !== btn.current) setOpen(false);
          }}
        >
          <button type="button" role="menuitem" onClick={() => (setOpen(false), void send("hide", hideRef, t("memories.hidden", { title })))}>
            {t("memories.hide")}
          </button>
          {fewer ? (
            <button type="button" role="menuitem" onClick={() => (setOpen(false), void send("fewer", `tag:${fewer.key}`, t("memories.fewerDone", { name: fewer.name })))}>
              {t("memories.fewer", { name: fewer.name })}
            </button>
          ) : null}
        </div>
      ) : null}
    </span>
  );
}
