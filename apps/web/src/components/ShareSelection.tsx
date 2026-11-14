// 「分享 N 張…」 (ADR-0054, audit round 124): the chosen photos as 2048 px JPEGs without where they were taken. On the
// desktop they go into a new folder inside one you pick (never inside the library), which then opens; on the web the
// share sheet takes them, or one ZIP is saved.
import { useState } from "react";
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
  run = (list) => shareMany(list, undefined, today()),
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
  const [done, setDone] = useState<Exclude<ManyResult, { kind: "cancelled" }> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const n = ids?.length ?? count ?? 0;
  const reason = !ids || n > SHARE_MAX ? { code: "SHARE_TOO_MANY", message: t("share.tooMany", { max: SHARE_MAX }) } : busy ? { code: "BUSY", message: t("share.preparing") } : null;
  const go = async () => {
    if (!ids) return;
    setBusy(true);
    setDone(null);
    setError(null);
    try {
      const r = await run(ids);
      if (r.kind !== "cancelled") setDone(r);
    } catch (e) {
      // the shell rejects with a plain string, not an Error (audit round 125, SHARE-023)
      const code = e instanceof Error ? e.message : String(e);
      setError(code === "SHARE_IN_ROOT" ? t("share.inRoot") : t("share.failed"));
    } finally {
      setBusy(false);
    }
  };
  const fmt = (x: number) => x.toLocaleString(i18n.language);
  return (
    <>
      <Button variant="ghost" disabledReason={reason} aria-busy={busy || undefined} onClick={() => void go()}>
        {t("share.many", { n: fmt(n) })}
      </Button>
      {done
        ? // a toast over the page, not a line inside the selection bar (SHARE-025)
          createPortal(
            <p role="status" className="toast flex flex-wrap items-center gap-2 text-sm">
              <span>
                {done.kind === "saved" ? t("share.savedTo", { n: fmt(done.n), dir: folderName(done.dir) }) : done.kind === "zipped" ? t("share.zipped", { name: done.name }) : t("share.shared", { n: fmt(done.n) })}
                {done.failed ? ` · ${t("share.someFailed", { n: fmt(done.failed) })}` : ""}
              </span>
              {done.kind === "saved" && open ? (
                <button type="button" className="chip" onClick={() => void open(done.handle)}>
                  {t("share.openFolder")}
                </button>
              ) : null}
              <button type="button" className="underline" aria-label={t("share.dismiss")} onClick={() => setDone(null)}>
                ×
              </button>
            </p>,
            document.body,
          )
        : null}
      {error ? (
        <p role="alert" className="text-sm text-[var(--danger,#e5484d)]">
          {error}
        </p>
      ) : null}
    </>
  );
}
