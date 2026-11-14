// 匯入 (ADR-0029): see what a folder holds, add it, and it is analysed in the background. Nothing here touches a file;
// organizing into folders is a separate step that goes through a plan (invariant 3).
import { useMutation } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "../../components/Button";
import { FolderPicker } from "../../components/FolderPicker";
import { LibraryFolders } from "../../components/LibraryFolders";
import { ProblemCard, type Problem } from "../../components/ProblemCard";
import { ApiError, api, type LibrarySource } from "../../lib/api";

export function ImportPage() {
  const { t } = useTranslation();
  const nav = useNavigate();
  const [blocked, setBlocked] = useState<{ source: string; problems: Problem[] } | null>(null);
  const add = useMutation({
    mutationFn: async ({ root, path }: { root: string; path: string }) => {
      const s = await api.createSource(root, path);
      try {
        await api.analyzeSource(s.id);
        void nav({ to: "/" });
      } catch (e) {
        const body = e instanceof ApiError ? (e.body as { blockers?: Problem[]; detail?: { blockers?: Problem[] } } | null) : null;
        const problems = body?.blockers ?? body?.detail?.blockers;
        if (!problems) throw e;
        setBlocked({ source: s.id, problems });
      }
    },
  });
  // a folder already in the library: analyse what is new, or everything again after a model or setting changed
  // stays here and says what happens: it used to jump to the timeline even when nothing was left (IMP-035)
  const [said, setSaid] = useState<string | null>(null);
  const again = useMutation({
    mutationFn: async (s: LibrarySource) => {
      await api.analyzeSource(s.id);
      const name = s.sub_path ? s.sub_path.split("/").at(-1)! : s.root_alias;
      setSaid(t(s.analysed >= s.photos ? "library.upToDate" : "library.analysing", { name }));
    },
  });
  const scanOnly = useMutation({
    mutationFn: async (source: string) => {
      await api.scanSource(source);
      void nav({ to: "/" });
    },
  });
  return (
    <div className="mx-auto max-w-5xl space-y-6 p-8">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold">{t("library.importTitle")}</h1>
        <p className="text-[var(--text-muted)]">{t("library.importLede")}</p>
      </header>
      {blocked ? (
        <div className="space-y-3" role="alert">
          {blocked.problems.map((p, i) => (
            <ProblemCard key={`${p.code}-${i}`} problem={p} />
          ))}
          <Button variant="ghost" disabledReason={scanOnly.isPending ? { code: "BUSY", message: t("common.working") } : null} onClick={() => scanOnly.mutate(blocked.source)}>
            {t("library.scanOnly")}
          </Button>
        </div>
      ) : null}
      <section className="space-y-2">
        <h2 className="font-semibold">{t("library.inLibrary")}</h2>
        <LibraryFolders
          action={(s) => t(s.analysed === 0 ? "library.analyse" : s.analysed < s.photos ? "library.analyseRest" : "library.reanalyse")}
          busy={again.isPending ? again.variables?.id : null}
          onAct={(s) => again.mutate(s)}
        />
        {said ? (
          <p role="status" className="text-sm text-[var(--text-muted)]">
            {said}
          </p>
        ) : null}
      </section>
      <h2 className="font-semibold">{t("library.orAddNew")}</h2>
      <FolderPicker title={t("library.pickTitle")} action={t("library.addAndAnalyze")} onPick={(root, path) => add.mutate({ root, path })} />
    </div>
  );
}
