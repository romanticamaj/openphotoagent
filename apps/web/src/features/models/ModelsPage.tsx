// Models (06 §6.5): the list with one CompatBadge per model (its best variant), and the model card with every
// variant's size, VRAM/RAM, speed (estimate or measured), licence and capabilities (MDL-005/006).
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { CaptionIndex } from "./CaptionIndex";
import { CompatBadge } from "../../components/CompatBadge";
import { api, type ModelView } from "../../lib/api";
import { formatBytes } from "../../lib/format";

function useWorker() {
  const roots = useQuery({ queryKey: ["roots"], queryFn: api.roots });
  return roots.data?.[0]?.worker_id;
}

export function ModelsPage() {
  const { t } = useTranslation();
  const wid = useWorker();
  const models = useQuery({ queryKey: ["models", wid], queryFn: () => api.models(wid!), enabled: Boolean(wid) });
  // face models stay out of sight until the person turns faces on (07 §6.2 F1)
  const faces = useQuery({ queryKey: ["face-settings"], queryFn: api.faceSettings, staleTime: 30_000 });
  return (
    <section className="mx-auto max-w-4xl space-y-4 p-8">
      <h1 className="text-xl font-semibold">{t("models.title")}</h1>
      <CaptionIndex />
      <table className="w-full text-left text-sm">
        <thead>
          <tr>
            <th>{t("models.name")}</th>
            <th>{t("models.kind")}</th>
            <th>{t("models.status")}</th>
          </tr>
        </thead>
        <tbody>
          {(models.data?.items ?? []).filter((m) => faces.data?.enabled || !m.kind.startsWith("face_")).map((m) => {
            const best = m.variants.find((v) => v.id === m.best_variant);
            return (
              <tr key={m.id} className="border-t border-[var(--border)]">
                <td className="py-2">
                  <Link to="/models/$modelId" params={{ modelId: m.id }} className="underline">
                    {m.display_name}
                  </Link>
                </td>
                <td>{m.kind}</td>
                <td>
                  <CompatBadge compat={best?.compat} />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}

export function ModelCard({ modelId }: { modelId: string }) {
  const { t, i18n } = useTranslation();
  const wid = useWorker();
  const q = useQuery({ queryKey: ["model", modelId, wid], queryFn: () => api.model(modelId, wid!), enabled: Boolean(wid) });
  const m: ModelView | undefined = q.data;
  if (!m) return <p role="status" className="p-8">{t("common.working")}</p>;
  return (
    <article className="mx-auto max-w-4xl space-y-4 p-8" aria-labelledby="model-h">
      <Link to="/models" className="text-sm underline">
        {t("models.back")}
      </Link>
      <h1 id="model-h" className="text-xl font-semibold">{m.display_name}</h1>
      <p data-testid="license">{t("models.license", { spdx: m.license.spdx })}</p>
      <p data-testid="capabilities">{t("models.capabilities", { list: m.capabilities.join("、") })}</p>
      <h2 className="font-semibold">{t("models.variants")}</h2>
      <ul className="space-y-3">
        {m.variants.map((v) => {
          const rates = Object.values(v.perf?.prior?.items_per_s ?? {})[0] as [number, number] | undefined;
          const vram = (v.vram_gb.weights ?? 0) + (v.vram_gb.mmproj ?? 0) + (v.vram_gb.runtime_overhead ?? 0);
          const inst = v.installation?.status ?? "none";
          return (
            <li key={v.id} data-testid={`variant-${v.id}`} className="grid grid-cols-[1fr_auto] gap-1 rounded-[var(--radius-md)] border border-[var(--border)] p-3">
              <strong>
                {v.id} <span className="text-[var(--text-muted)]">{v.runtime}</span>
              </strong>
              <CompatBadge compat={v.compat} />
              <span data-testid="size" className="tnum">
                {t("models.size", { size: formatBytes(v.size_bytes, i18n.language) })}
                {v.size_estimated ? ` ${t("models.estimated")}` : ""}
              </span>
              <span data-testid="installed">{t(`models.installed.${inst}`, { defaultValue: inst })}</span>
              <span data-testid="vram" className="tnum">{t("models.vram", { gb: vram.toFixed(1) })}</span>
              <span data-testid="ram" className="tnum">{v.ram_gb != null ? t("models.ram", { gb: Number(v.ram_gb).toFixed(1) }) : ""}</span>
              {rates && (
                <span data-testid="speed" className="tnum">
                  {t("models.speed", { lo: rates[0], hi: rates[1] })} ·{" "}
                  {v.perf.basis === "observed" ? t("models.speedObserved") : t("models.speedEstimated")}
                </span>
              )}
            </li>
          );
        })}
      </ul>
    </article>
  );
}
