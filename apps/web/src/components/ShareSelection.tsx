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
}: {
  /** the chosen photos; null when they are not listed one by one (all selected) */
  ids: string[] | null;
  /** how many are chosen, when they are not listed (all selected): the label says it, not 「0 張」 (SHARE-024) */
  count?: number;
  run?: (ids: string[]) => Promise<ManyResult>;
  open?: (handle: string) => Promise<string>;
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
  const go = async () => {
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
      <Button ref={shareRef} variant="ghost" disabledReason={reason} aria-busy={busy || undefined} onClick={() => void go()}>
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
