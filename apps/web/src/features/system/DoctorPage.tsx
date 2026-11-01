// System check (06 §6.6): summary, then needs-action → attention → OK (collapsed), each problem with its fixes.
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button } from "../../components/Button";
import { ProblemCard } from "../../components/ProblemCard";
import { api } from "../../lib/api";
import { checkKey } from "../../lib/i18n";

export function DoctorPage() {
  const { t } = useTranslation();
  const roots = useQuery({ queryKey: ["roots"], queryFn: api.roots });
  const wid = roots.data?.[0]?.worker_id;
  const doc = useQuery({ queryKey: ["doctor", wid], queryFn: () => api.doctor(wid!), enabled: Boolean(wid) });
  const rs = doc.data?.results ?? [];
  const by = (s: string) => rs.filter((r) => r.status === s);
  const title = (id: string) => t(`checks.${checkKey(id)}.title`, { defaultValue: id });
  return (
    <section className="mx-auto max-w-3xl space-y-6 p-8" aria-labelledby="doctor-h">
      <div className="flex items-center gap-4">
        <h1 id="doctor-h" className="text-xl font-semibold">{t("doctor.title")}</h1>
        <Button variant="ghost" onClick={() => void doc.refetch()}>
          {t("doctor.recheck")}
        </Button>
      </div>
      <p data-testid="doctor-summary" className="tnum">
        {t("doctor.summary", { ok: by("ok").length, warn: by("warn").length, fail: by("fail").length + by("crashed").length })}
      </p>
      {(["fail", "warn"] as const).map((s) =>
        by(s).length ? (
          <section key={s} aria-labelledby={`h-${s}`} className="space-y-2">
            <h2 id={`h-${s}`} className="font-semibold">
              {s === "fail" ? "● " : "⚠ "}
              {t(`doctor.${s}`)}
            </h2>
            {by(s).map((r) =>
              r.code ? (
                <ProblemCard key={r.check_id} problem={{ code: r.code, params: r.params, fixes: r.fixes }} tone={s === "fail" ? "danger" : "warning"} />
              ) : (
                <p key={r.check_id}>{title(r.check_id)}</p>
              ),
            )}
          </section>
        ) : null,
      )}
      <details>
        <summary className="cursor-pointer font-semibold">
          ✓ {t("doctor.ok")} ({by("ok").length})
        </summary>
        <ul className="mt-2 space-y-1 text-sm">
          {by("ok").map((r) => (
            <li key={r.check_id}>{title(r.check_id)}</li>
          ))}
        </ul>
      </details>
    </section>
  );
}
