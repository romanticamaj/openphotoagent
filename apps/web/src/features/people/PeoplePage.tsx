// People (07 §4): off until the person opts in (never blocking), a wall of named and unnamed people, a keyboard
// review loop 「這是不是 X？」 (least certain first) and Settings › 人臉 with consent and disable-with-delete.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useLocation } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "../../components/Button";
import { api, type PurgeReport } from "../../lib/api";

export function useFaceSettings() {
  return useQuery({ queryKey: ["face-settings"], queryFn: api.faceSettings, staleTime: 30_000 });
}

/** Photos page card (FACE-GATE-004): one tap to skip, never shown again; Settings keeps the switch. */
export function FaceOptInCard() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const loc = useLocation();
  const s = useFaceSettings();
  const later = useMutation({ mutationFn: api.dismissFacePrompt, onSuccess: (d) => qc.setQueryData(["face-settings"], d) });
  if (loc.pathname !== "/" || !s.data || s.data.enabled || s.data.prompt_dismissed) return null;
  return (
    <aside data-testid="faces-optin-card" className="mb-3 flex items-center justify-between rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--surface)] p-3 text-sm">
      <span>{t("faces.card")}</span>
      <span className="flex items-center gap-2">
        <Link to="/settings/faces" className="underline">{t("faces.learnMore")}</Link>
        <Button variant="ghost" onClick={() => later.mutate()}>{t("faces.later")}</Button>
      </span>
    </aside>
  );
}

export function PeoplePage() {
  const { t } = useTranslation();
  const q = useQuery({ queryKey: ["people"], queryFn: api.people });
  return (
    <section className="space-y-4 p-6" aria-labelledby="people-h">
      <header className="flex items-center justify-between">
        <h1 id="people-h" className="text-xl font-semibold">{t("faces.people")}</h1>
        <Link to="/people/review" className="underline">{t("faces.review")}</Link>
      </header>
      <ul className="grid grid-cols-[repeat(auto-fill,minmax(10rem,1fr))] gap-3">
        {(q.data?.people ?? []).map((p) => (
          <li key={p.id} data-testid="person" className="rounded-[var(--radius-md)] border border-[var(--border)] p-3">
            <p className="font-medium" translate="no">{p.name ?? t("faces.unnamed")}</p>
            <p className="text-xs text-[var(--text-muted)]">{t("faces.count", { n: p.asset_count })}</p>
          </li>
        ))}
      </ul>
      {(q.data?.others ?? 0) > 0 && <p className="text-sm text-[var(--text-muted)]">{t("faces.others", { n: q.data!.others })}</p>}
      <p className="text-xs text-[var(--text-muted)]">{t("faces.transparency")}</p>
    </section>
  );
}

/** 「這是不是 X？」: Y confirm · N not this person · S skip (FACE-REV-001). */
export function FaceReview() {
  const { t } = useTranslation();
  const q = useQuery({ queryKey: ["face-review"], queryFn: api.faceReview });
  const [i, setI] = useState(0);
  const items = q.data ?? [];
  const cur = items[i];
  const next = () => setI((x) => x + 1);
  const yes = useMutation({ mutationFn: () => api.confirmFace(cur.face_id, cur.person_id), onSuccess: next });
  const no = useMutation({ mutationFn: () => api.rejectFace(cur.face_id, cur.person_id), onSuccess: next });
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (!cur || e.ctrlKey || e.metaKey || e.altKey) return;
      const k = e.key.toLowerCase();
      if (k === "y") yes.mutate();
      else if (k === "n") no.mutate();
      else if (k === "s") next();
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [cur, yes, no]);
  if (q.isLoading) return <p role="status" className="p-6">{t("common.loading")}</p>;
  if (!cur)
    return (
      <p data-testid="face-review-done" className="p-6">
        {t("faces.reviewDone")}
      </p>
    );
  return (
    <section className="space-y-3 p-6" aria-live="polite">
      <h1 data-testid="face-question" className="text-xl font-semibold">
        {t("faces.isThis", { name: cur.person ?? t("faces.unnamed") })}
      </h1>
      <p className="text-sm text-[var(--text-muted)]">
        {t("faces.score")} <span data-testid="face-score" className="tnum">{(cur.score ?? 0).toFixed(2)}</span>
      </p>
      <p className="text-sm">{t("faces.keys")}</p>
      <div className="flex gap-2">
        <Button onClick={() => yes.mutate()}>{t("faces.yes")}</Button>
        <Button variant="ghost" onClick={() => no.mutate()}>{t("faces.no")}</Button>
        <Button variant="ghost" onClick={next}>{t("faces.skip")}</Button>
      </div>
    </section>
  );
}

const FACE_MODELS = ["yunet-2023mar@onnx-fp32", "auraface-v1@onnx-fp32"];

/** Settings › 人臉: opt-in with consent; disabling ticks 「同時刪除」 by default (FACE-PRG-004). */
export function FaceSettingsPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const s = useFaceSettings();
  const [wizard, setWizard] = useState(false);
  const [agree, setAgree] = useState(false);
  const [off, setOff] = useState(false);
  const [del, setDel] = useState(true);
  const [name, setName] = useState("");
  const [report, setReport] = useState<PurgeReport | null>(null);
  const refresh = () => void qc.invalidateQueries({ queryKey: ["face-settings"] });
  const enable = useMutation({
    mutationFn: async () => {
      const on = await api.enableFaces();
      // AuraFace is 260 MB, too big for the installer: the models arrive with the person's consent (07 §1.4)
      const worker = (await api.roots())[0]?.worker_id;
      if (worker) await api.installCombo(worker, FACE_MODELS);
      return on;
    },
    onSuccess: () => (setWizard(false), refresh()),
  });
  const disable = useMutation({
    mutationFn: () => api.disableFaces(del, name),
    onSuccess: (r) => (setOff(false), setReport(r.purged), refresh(), void qc.invalidateQueries({ queryKey: ["people"] })),
  });
  return (
    <section className="mx-auto max-w-2xl space-y-4 p-8" aria-labelledby="faces-h">
      <h1 id="faces-h" className="text-xl font-semibold">{t("faces.settings")}</h1>
      <p className="text-sm text-[var(--text-muted)]">{t("faces.explain")}</p>
      {s.data?.enabled ? (
        <Button variant="ghost" onClick={() => setOff(true)}>{t("faces.disable")}</Button>
      ) : (
        <Button onClick={() => setWizard(true)}>{t("faces.enable")}</Button>
      )}
      {report && (
        <p role="status" data-testid="purge-report">
          {t("faces.purged", { faces: report.faces, persons: report.persons })}
        </p>
      )}
      {wizard && (
        <div role="dialog" aria-label={t("faces.enable")} className="space-y-3 rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--surface)] p-4">
          <p className="text-sm">{t("faces.consent")}</p>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={agree} onChange={(e) => setAgree(e.target.checked)} />
            {t("faces.agree")}
          </label>
          <Button disabledReason={agree ? null : { code: "FACE_CONSENT_REQUIRED", message: t("faces.needConsent") }} onClick={() => enable.mutate()}>
            {t("faces.agreeAndEnable")}
          </Button>
        </div>
      )}
      {off && (
        <div role="dialog" aria-label={t("faces.disable")} className="space-y-3 rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--surface)] p-4">
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={del} onChange={(e) => setDel(e.target.checked)} />
            {t("faces.alsoDelete")}
          </label>
          {del && (
            <label className="block text-sm">
              {t("faces.typeName", { name: s.data?.workspace_name ?? "" })}
              <input className="ml-2 rounded border border-[var(--border)] px-2" value={name} onChange={(e) => setName(e.target.value)} />
            </label>
          )}
          <Button
            disabledReason={del && name !== s.data?.workspace_name ? { code: "CONFIRM_TEXT_MISMATCH", message: t("faces.typeNameFirst") } : null}
            onClick={() => disable.mutate()}
          >
            {del ? t("faces.disableAndDelete") : t("faces.disableOnly")}
          </Button>
        </div>
      )}
    </section>
  );
}
