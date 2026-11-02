// Settings → AI 問照片 (10 §8.2, §8.9): local model or an external OpenAI-compatible endpoint. Choosing the
// external service is explicit, and what leaves the machine is said right here (ASK-PRV-001..004).
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, type AssistantSettings } from "../../lib/api";

export function AssistantSettingsPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["assistant-settings"], queryFn: api.assistantSettings });
  const [draft, setDraft] = useState<Partial<AssistantSettings>>({});
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    if (q.data) setDraft({ provider: q.data.provider, endpoint_url: q.data.endpoint_url, retention_days: q.data.retention_days });
  }, [q.data]);
  const save = async () => {
    await api.putAssistantSettings({
      provider: draft.provider,
      endpoint_url: draft.provider === "openai_compat" ? (draft.endpoint_url ?? "") : null,
      retention_days: draft.retention_days,
    });
    await qc.invalidateQueries({ queryKey: ["assistant-settings"] });
    setSaved(true);
  };
  return (
    <section className="mx-auto max-w-2xl space-y-5 p-8" aria-labelledby="assistant-h">
      <h1 id="assistant-h" className="text-xl font-semibold">
        {t("ask.settings.title")}
      </h1>
      <fieldset className="space-y-2">
        <legend className="font-medium">{t("ask.settings.provider")}</legend>
        <label className="flex items-center gap-2">
          <input type="radio" name="provider" checked={draft.provider !== "openai_compat"} onChange={() => setDraft({ ...draft, provider: "local" })} />
          {t("ask.settings.local")}
        </label>
        <label className="flex items-center gap-2">
          <input type="radio" name="provider" checked={draft.provider === "openai_compat"} onChange={() => setDraft({ ...draft, provider: "openai_compat" })} />
          {t("ask.settings.external")}
        </label>
      </fieldset>
      {draft.provider === "openai_compat" ? (
        <>
          <label className="flex flex-col gap-1">
            {t("ask.settings.endpoint")}
            <input
              type="url"
              value={draft.endpoint_url ?? ""}
              onChange={(e) => setDraft({ ...draft, endpoint_url: e.target.value })}
              className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--bg)] px-2 py-1"
            />
          </label>
          <p role="note" className="rounded-[var(--radius-sm)] border border-[var(--border)] p-2 text-sm">
            {t("ask.settings.cloudNote")}
          </p>
        </>
      ) : null}
      <label className="flex flex-col gap-1">
        {t("ask.settings.retention")}
        <input
          type="number"
          min={0}
          max={3650}
          value={draft.retention_days ?? 90}
          onChange={(e) => setDraft({ ...draft, retention_days: Number(e.target.value) })}
          className="w-32 rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--bg)] px-2 py-1"
        />
      </label>
      <button type="button" onClick={() => void save()} className="rounded-[var(--radius-md)] bg-[var(--accent)] px-4 py-2 text-[var(--accent-fg)]">
        {t("ask.settings.save")}
      </button>
      {saved ? (
        <p data-testid="assistant-saved" role="status">
          {t("ask.settings.saved")}
        </p>
      ) : null}
    </section>
  );
}
