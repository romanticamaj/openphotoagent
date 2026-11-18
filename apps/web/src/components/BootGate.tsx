import { type ReactNode, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { type BootStatus, platform } from "../lib/platform";
import { Button } from "./Button";

/** The first thing on screen on the desktop (08 §1, DESK-001): the sidecars take seconds to start, and when one cannot
 * start there is no API left to ask, so the shell reports it here. The web build has no shell and renders straight on. */
export function BootGate({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const p = platform();
  const [ready, setReady] = useState(!p.backend);
  const [status, setStatus] = useState<BootStatus | null>(null);

  useEffect(() => {
    if (!p.backend) return;
    let live = true;
    void p.backend().then(() => {
      if (live) setReady(true);
    });
    return () => {
      live = false;
    };
  }, [p]);

  useEffect(() => {
    if (!p.status) return;
    const tick = () => void p.status!().then(setStatus);
    tick();
    const id = setInterval(tick, ready ? 2000 : 300);
    return () => clearInterval(id);
  }, [p, ready]);

  if (status?.fatal) {
    const report = [status.fatal, status.detail, status.logs].filter(Boolean).join("\n");
    return (
      <main role="alert" className="mx-auto max-w-xl space-y-4 p-12">
        <h1 className="text-xl font-semibold">{t("boot.failed")}</h1>
        <p className="text-[var(--text-muted)]">{t("boot.failedHelp")}</p>
        <p className="font-mono text-sm">{status.fatal}</p>
        {status.detail && <pre className="max-h-64 overflow-auto whitespace-pre-wrap text-xs">{status.detail}</pre>}
        <p className="text-sm">
          {t("boot.logs")}: <code>{status.logs}</code>
        </p>
        <Button onClick={() => void navigator.clipboard.writeText(report)}>{t("boot.copy")}</Button>
      </main>
    );
  }
  if (!ready) {
    return (
      <main className="grid h-screen place-items-center">
        <p role="status" className="text-[var(--text-muted)]">{t("boot.starting")}</p>
      </main>
    );
  }
  return (
    <>
      {status?.reconnecting && (
        <p data-testid="reconnecting" role="status" className="bg-[var(--surface-2)] px-4 py-1 text-center text-sm">
          {t("boot.reconnecting")}
        </p>
      )}
      {children}
    </>
  );
}
