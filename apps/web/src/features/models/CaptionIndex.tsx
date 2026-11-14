// R2 caption-vector index on the Models page (05 §2; SRCH-R2-004; ADR-0014): pick the text model, build the index
// (an Operation with ETA and elapsed time), and switch to a newly built index once it is complete — until then the
// switch says why it is not available (invariant 8).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "../../components/Button";
import { api } from "../../lib/api";
import { OperationProgress } from "../activity/OperationProgress";
import type { Op } from "../activity/model";

const MODELS = ["qwen3-embedding-0.6b", "multilingual-e5-small"];

export function CaptionIndex() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const st = useQuery({ queryKey: ["caption-index"], queryFn: api.captionIndex, refetchInterval: 1000 });
  const ops = useQuery({ queryKey: ["operations", "all"], queryFn: () => api.operations(), refetchInterval: 1000 });
  const [model, setModel] = useState(MODELS[0]);
  const refresh = () => void qc.invalidateQueries({ queryKey: ["caption-index"] });
  const build = useMutation({ mutationFn: () => api.buildCaptionIndex(model), onSuccess: refresh });
  const sw = useMutation({ mutationFn: api.switchCaptionIndex, onSuccess: refresh });
  const top = useMutation({ mutationFn: api.topupCaptionIndex, onSuccess: refresh });
  const b = st.data?.building ?? null;
  const op = b?.operation_id ? (ops.data ?? []).find((o) => o.id === b.operation_id) : undefined;
  return (
    <section role="region" aria-label={t("captionIndex.title")} className="space-y-3 rounded-[var(--radius-md)] border border-[var(--border)] p-4">
      <h2 className="font-semibold">{t("captionIndex.title")}</h2>
      <p className="text-sm text-[var(--text-muted)]">{t("captionIndex.help")}</p>
      <p data-testid="caption-active" className="text-sm">
        {st.data?.active ? t("captionIndex.active", { model: st.data.active.model_key, n: st.data.active.count.toLocaleString() }) : t("captionIndex.none")}
      </p>
      {st.data?.active?.missing ? (
        // photos analysed since it was built (SRCH-R2-013): said, with the one button that adds them
        <p data-testid="caption-missing" className="flex flex-wrap items-center gap-2 text-sm">
          <span>{t("captionIndex.missing", { n: st.data.active.missing.toLocaleString() })}</span>
          <Button variant="ghost" onClick={() => top.mutate()}>{t("captionIndex.update")}</Button>
        </p>
      ) : null}
      <div className="flex flex-wrap items-end gap-2">
        <label className="flex flex-col gap-1 text-sm">
          {t("captionIndex.model")}
          <select value={model} onChange={(e) => setModel(e.target.value)} className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--bg)] px-2 py-1">
            {MODELS.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </label>
        <Button onClick={() => build.mutate()}>{t("captionIndex.build")}</Button>
      </div>
      {b ? (
        <div className="space-y-2">
          <p className="text-sm">{t("captionIndex.building", { model: b.model_key })}</p>
          {/* one bar: the Operation's once it is in (time left included), a plain one until then (SRCH-R2-012) */}
          {op ? (
            <OperationProgress op={op as unknown as Op} />
          ) : (
            <>
              <progress max={Math.max(1, b.total)} value={b.done} aria-label={t("captionIndex.progress")} className="w-full" />
              <p className="tnum text-xs text-[var(--text-muted)]">
                {b.done.toLocaleString()} / {b.total.toLocaleString()}
              </p>
            </>
          )}
          <Button
            variant="ghost"
            disabledReason={b.ready ? null : { code: "REINDEX_NOT_READY", message: t("captionIndex.notReady") }}
            onClick={() => sw.mutate()}
          >
            {t("captionIndex.switch")}
          </Button>
        </div>
      ) : null}
    </section>
  );
}
