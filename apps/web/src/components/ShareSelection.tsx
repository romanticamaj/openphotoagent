// 「分享 N 張…」 (ADR-0054, audit round 124): the chosen photos as 2048 px JPEGs without where they were taken. On the
// desktop they go into a new folder inside one you pick (never inside the library), which then opens; on the web the
// share sheet takes them, or one ZIP is saved.
import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { platform } from "../lib/platform";
import { SHARE_MAX, shareMany, type ManyResult } from "../lib/shareMany";
import { Button } from "./Button";

/** The local day, for the folder's name. */
function today(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** The folder's own name: the path pushed the bar's buttons onto a second row (audit round 125, SHARE-025). */
function folderName(dir: string): string {
  return dir.split(/[\\/]/).filter(Boolean).pop() ?? dir;
}

export function ShareSelection({
  ids,
  count,
  run,
  open = platform().openShared,
  check,
}: {
  /** the chosen photos; null when they are not listed one by one (all selected) */
  ids: string[] | null;
  /** how many are chosen, when they are not listed (all selected): the label says it, not 「0 張」 (SHARE-024) */
  count?: number;
  run?: (ids: string[]) => Promise<ManyResult>;
  open?: (handle: string) => Promise<string>;
  /** which of them have a face, when faces are on (FACE-SHR-001): asked before the share, never who */
  check?: (ids: string[]) => Promise<{ enabled: boolean; with_faces?: string[] }>;
}) {
  const { t, i18n } = useTranslation();
  const [busy, setBusy] = useState(false);
  // how far a share is, once its folder is chosen: 準備中… sat for 4 s on large photos (round 132, SHARE-033)
  const [made, setMade] = useState<{ done: number; total: number } | null>(null);
  const share = run ?? ((list: string[]) => shareMany(list, undefined, today(), (done, total) => setMade({ done, total })));
  const [done, setDone] = useState<Exclude<ManyResult, { kind: "cancelled" }> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const n = ids?.length ?? count ?? 0;
  const reason = !ids || n > SHARE_MAX ? { code: "SHARE_TOO_MANY", message: t("share.tooMany", { max: SHARE_MAX }) } : null;
  const shareRef = useRef<HTMLButtonElement>(null);
  const toastRef = useRef<HTMLParagraphElement>(null);
  // the toast takes the keyboard as it comes: Tab could not reach 開啟資料夾 (audit round 126, SHARE-026)
  useEffect(() => {
    if (done || error) toastRef.current?.querySelector("button")?.focus();
  }, [done, error]);
  const close = () => {
    setDone(null);
    setError(null);
    shareRef.current?.focus();
  };
  // Esc closes the toast only; it bubbled to the bar and cleared the selection, and focus was lost (SHARE-026)
  const onToastKey = (e: KeyboardEvent) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      close();
      return;
    }
    // Tab goes round the toast's buttons and back to 分享, not to the top of the page (audit round 127, SHARE-030)
    if (e.key !== "Tab") return;
    const bs = [...(toastRef.current?.querySelectorAll("button") ?? [])];
    const at = bs.indexOf(document.activeElement as HTMLButtonElement);
    if ((e.shiftKey && at === 0) || (!e.shiftKey && at === bs.length - 1)) {
      e.preventDefault();
      e.stopPropagation();
      shareRef.current?.focus();
    }
  };
  // faces in the chosen photos, said before they leave (ADR-0049 addendum 8, SHARE-037): 13 of 14 memory covers had
  // faces and went with no word. Only when faces are on; nothing is guessed when they are off
  const [faced, setFaced] = useState<string[] | null>(null);
  // the check could not answer: asked, not shared unseen (round 141, SHARE-038)
  const [unsure, setUnsure] = useState(false);
  // 全部分享 with faces asks once more: one Shift+Tab and Enter shared them (round 143, SHARE-041)
  const [sure, setSure] = useState(false);
  const sureRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (sure) sureRef.current?.focus();
  }, [sure]);
  const dialog = useRef<HTMLDivElement>(null);
  useEffect(() => {
    // the safer choice first: 只分享其他 N 張, else 取消 (round 141: focus was on 全部分享)
    if (faced || unsure) dialog.current?.querySelector<HTMLElement>("[data-safe]")?.focus();
  }, [faced, unsure]);
  const start = async () => {
    if (!ids || busy) return;
    if (check) {
      const r = await check(ids).catch(() => null);
      if (r === null) {
        setUnsure(true);
        return;
      }
      if (r.enabled && r.with_faces?.length) {
        setFaced(r.with_faces);
        return;
      }
    }
    void go(ids);
  };
  const shut = (back = true) => {
    setFaced(null);
    setUnsure(false);
    setSure(false);
    if (back) shareRef.current?.focus();
  };
  const go = async (ids: string[] | null) => {
    if (!ids || busy) return;
    setBusy(true);
    setDone(null);
    setError(null);
    try {
      const r = await share(ids);
      if (r.kind !== "cancelled") setDone(r);
    } catch (e) {
      // the shell rejects with a plain string, not an Error (audit round 125, SHARE-023)
      const code = e instanceof Error ? e.message : String(e);
      setError(code === "SHARE_IN_ROOT" ? t("share.inRoot") : t("share.failed"));
    } finally {
      setBusy(false);
      setMade(null);
    }
  };
  const fmt = (x: number) => x.toLocaleString(i18n.language);
  const widest = t("share.preparingN", { done: fmt(n), total: fmt(n) });
  return (
    <>
      {/* busy says so in its label: a reason line under it moved the bar's buttons (audit round 126, SHARE-027) */}
      <Button ref={shareRef} variant="ghost" disabledReason={reason} aria-busy={busy || undefined} onClick={() => void start()}>
        {busy ? (
          // the label it had holds its width while it prepares: the bar's buttons slid for 3 s (round 127, SHARE-029)
          <span className="inline-grid">
            {made || run ? (
              <span aria-hidden="true" className="invisible col-start-1 row-start-1">
                {t("share.many", { n: fmt(n) })}
              </span>
            ) : null}
            <span aria-hidden="true" className="share-hold col-start-1 row-start-1" data-hold={widest} />
            <span className="col-start-1 row-start-1">
              {made ? t("share.preparingN", { done: fmt(made.done), total: fmt(made.total) }) : run ? t("share.preparing") : t("share.many", { n: fmt(n) })}
            </span>
          </span>
        ) : (
          // the widest label it will have holds its place from the start, drawn by CSS and not text: 「準備中 3/3…」
          // widened it 10 px and moved the bar's next buttons (round 133, SHARE-034)
          <span className="inline-grid">
            <span aria-hidden="true" className="share-hold col-start-1 row-start-1" data-hold={widest} />
            <span className="col-start-1 row-start-1">{t("share.many", { n: fmt(n) })}</span>
          </span>
        )}
      </Button>
      {(faced || unsure) && ids
        ? createPortal(
            <div className="fixed inset-0 z-40 grid place-items-center bg-black/50">
              <div
                ref={dialog}
                role="alertdialog"
                aria-modal="true"
                aria-labelledby="share-faces-h"
                className="max-w-sm space-y-3 rounded-[var(--radius-lg)] bg-[var(--surface)] p-5"
                onKeyDown={(e) => {
                  if (e.key === "Escape") {
                    e.stopPropagation();
                    shut();
                    return;
                  }
                  // Tab goes round its buttons: it left the dialog, which says it is modal (round 141, SHARE-038)
                  if (e.key !== "Tab") return;
                  const bs = [...(dialog.current?.querySelectorAll<HTMLElement>("button") ?? [])];
                  const at = bs.indexOf(document.activeElement as HTMLElement);
                  e.preventDefault();
                  bs[(at + (e.shiftKey ? -1 : 1) + bs.length) % bs.length]?.focus();
                }}
              >
                <h2 id="share-faces-h" className="font-semibold">
                  {faced ? t("share.facesTitle", { n: fmt(ids.length), k: fmt(faced.length) }) : t("share.facesUnknown")}
                </h2>
                <p className="text-sm text-[var(--text-muted)]">{t("share.facesNote")}</p>
                <div className="flex flex-wrap gap-2">
                  {faced && ids.length > faced.length ? (
                    <Button data-safe="" onClick={() => (shut(false), void go(ids.filter((x) => !faced.includes(x))))}>
                      {t("share.facesOthers", { n: fmt(ids.length - faced.length) })}
                    </Button>
                  ) : null}
                  <Button variant="ghost" {...(faced && ids.length > faced.length ? {} : { "data-safe": "" })} onClick={() => shut()}>
                    {t("batch.cancel")}
                  </Button>
                  {/* the risky choice last, marked so: it came first and looked like the others (round 142, SHARE-040) */}
                  {faced && sure ? (
                    <Button ref={sureRef} variant="ghost" className="share-risky danger" onClick={() => (shut(false), void go(ids))}>
                      {t("share.facesSure")}
                    </Button>
                  ) : (
                    <Button variant="ghost" className="share-risky danger" onClick={() => (faced ? setSure(true) : (shut(false), void go(ids)))}>
                      {t(faced ? "share.facesAll" : "share.facesAnyway")}
                    </Button>
                  )}
                </div>
              </div>
            </div>,
            document.body,
          )
        : null}
      {done
        ? // a toast over the page, not a line inside the selection bar (SHARE-025)
          createPortal(
            <p ref={toastRef} role="status" className="toast flex flex-wrap items-center gap-2 text-sm" onKeyDown={onToastKey}>
              <span>
                {done.kind === "saved" ? t("share.savedTo", { n: fmt(done.n), dir: folderName(done.dir) }) : done.kind === "zipped" ? t("share.zipped", { name: done.name }) : t("share.shared", { n: fmt(done.n) })}
                {done.failed ? ` · ${t("share.someFailed", { n: fmt(done.failed) })}` : ""}
              </span>
              {done.kind === "saved" && open ? (
                <button type="button" className="chip" onClick={() => void open(done.handle)}>
                  {t("share.openFolder")}
                </button>
              ) : null}
              <button type="button" className="underline" aria-label={t("share.dismiss")} onClick={close}>
                ×
              </button>
            </p>,
            document.body,
          )
        : null}
      {error
        ? // a toast as well: a line in the bar moved its buttons (SHARE-027)
          createPortal(
            <p ref={toastRef} role="alert" className="toast flex items-center gap-2 text-sm text-[var(--danger,#e5484d)]" onKeyDown={onToastKey}>
              <span>{error}</span>
              <button type="button" className="underline" aria-label={t("share.dismiss")} onClick={close}>
                ×
              </button>
            </p>,
            document.body,
          )
        : null}
    </>
  );
}
