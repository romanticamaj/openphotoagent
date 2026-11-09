// People (07 §4): off until the person opts in (never blocking), a wall of named and unnamed people, a keyboard
// review loop 「這是不是 X？」 (least certain first) and Settings › 人臉 with consent and disable-with-delete.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useLocation } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "../../components/Button";
import { FaceCrop } from "../../components/FaceCrop";
import { Lightbox } from "../viewer/Lightbox";
import type { GridApi } from "../viewer/PhotoGrid";
import { api, mediaUrl, type FaceCandidate, type PersonRow, type PurgeReport } from "../../lib/api";

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

type Mark = "none" | "yes" | "no";
const PAGE = 12;
const NEXT: Record<Mark, Mark> = { none: "yes", yes: "no", no: "none" };

/** 「可能也是他？」: faces like the confirmed ones, best first. One click 是, another 不是; what is confirmed widens
 * the next search (docs/research/face-search-2026-11.md). */
function Candidates({ person }: { person: PersonRow }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["person-candidates", person.id], queryFn: () => api.personCandidates(person.id) });
  const [marks, setMarks] = useState<Record<string, Mark>>({});
  const [shown, setShown] = useState(PAGE);
  const all: FaceCandidate[] = q.data?.items ?? [];
  const items = all.slice(0, shown); // a page at a time: 「全部都是」 never reaches faces nobody looked at
  const pick = (m: Mark) => Object.keys(marks).filter((k) => marks[k] === m);
  const yes = pick("yes");
  const no = pick("no");
  const send = useMutation({
    mutationFn: () => api.reviewPerson(person.id, { yes, no }),
    onSuccess: () => {
      setMarks({});
      setShown(PAGE);
      for (const k of ["person-candidates", "person-photos"]) void qc.invalidateQueries({ queryKey: [k, person.id] });
      void qc.invalidateQueries({ queryKey: ["people"] });
    },
  });
  const who = person.name ?? t("faces.thisPerson");
  if (q.isSuccess && all.length === 0) return <p className="text-sm text-[var(--text-muted)]">{t("faces.noCandidates")}</p>;
  if (!items.length) return null;
  return (
    <section aria-labelledby={`cand-${person.id}`} className="space-y-2">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 id={`cand-${person.id}`} className="font-semibold">{t("faces.candidatesTitle", { name: who, n: all.length })}</h3>
        <p className="text-xs text-[var(--text-muted)]">{t("faces.candidatesHint")}</p>
      </div>
      <ul className="candidate-grid">
        {items.map((c) => {
          const m = marks[c.face_id] ?? "none";
          return (
            <li key={c.face_id}>
              <button
                type="button"
                data-testid="candidate"
                data-mark={m}
                aria-label={t(`faces.mark.${m}`)}
                title={t("faces.similarity", { n: Math.round(c.score * 100) })}
                className="candidate"
                onClick={() => setMarks((x) => ({ ...x, [c.face_id]: NEXT[m] }))}
              >
                <FaceCrop assetId={c.asset_id} ch={c.ch} bbox={c.bbox} />
                {m !== "none" ? <span aria-hidden="true" className="candidate-mark">{m === "yes" ? "✓" : "✕"}</span> : null}
              </button>
            </li>
          );
        })}
      </ul>
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="ghost" onClick={() => setMarks(Object.fromEntries(items.map((c) => [c.face_id, "yes" as Mark])))}>
          {t("faces.allYes")}
        </Button>
        <Button
          onClick={() => send.mutate()}
          disabledReason={yes.length + no.length ? null : { code: "NOTHING_MARKED", message: t("faces.markFirst") }}
        >
          {t("faces.send", { yes: yes.length, no: no.length })}
        </Button>
        {all.length > shown ? (
          <button type="button" className="text-sm underline" onClick={() => setShown((n) => n + PAGE)}>
            {t("faces.more", { n: Math.min(PAGE, all.length - shown) })}
          </button>
        ) : null}
      </div>
    </section>
  );
}

/** One person: their photos, and the name that makes them findable. */
function PersonPanel({ person, onClose }: { person: PersonRow; onClose: () => void }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [name, setName] = useState(person.name ?? "");
  const photos = useQuery({
    queryKey: ["person-photos", person.id],
    queryFn: () => api.search({ q: null, filters: { persons_any: [person.id] }, limit: 120 }),
  });
  const hits = photos.data?.items ?? [];
  const [viewing, setViewing] = useState<number | null>(null);
  const grid: GridApi = useMemo(
    () => ({
      total: hits.length,
      ordinal: (p) => p.index,
      at: async (i) => {
        const h = hits[i];
        if (!h) return null;
        return { asset: { id: h.asset_id, ch: h.ch, w: null, h: null, thumbhash: null, taken_at: h.taken_at, flags: [] }, pos: { bucket: 0, index: i } };
      },
    }),
    [hits],
  );
  const save = useMutation({
    mutationFn: () => api.namePerson(person.id, name.trim()),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["people"] }),
  });
  return (
    <section aria-label={person.name ?? t("faces.unnamed")} className="space-y-3 rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--surface)] p-4">
      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <label className="flex flex-col gap-1 text-sm">
          {t("faces.whoIsThis")}
          <input value={name} onChange={(e) => setName(e.target.value)} className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--bg)] px-2 py-1" />
        </label>
        <Button type="submit" disabledReason={name.trim() ? null : { code: "NAME_REQUIRED", message: t("faces.nameFirst") }}>
          {t("faces.saveName")}
        </Button>
        <button type="button" className="ml-auto text-sm underline" onClick={onClose}>{t("dock.close")}</button>
      </form>
      <h3 className="font-semibold">{t("faces.photos", { n: photos.data?.total ?? 0 })}</h3>
      <div className="explore-grid">
        {hits.map((h, i) => (
          <button key={h.asset_id} type="button" className="explore-tile" aria-label={h.caption ?? t("faces.openPhoto")} onClick={() => setViewing(i)}>
            <img src={mediaUrl(h.asset_id, "t256", h.ch)} alt="" loading="lazy" />
            <span className="explore-caption">{h.caption ?? ""}</span>
          </button>
        ))}
      </div>
      {viewing !== null ? <Lightbox start={viewing} grid={grid} onClose={() => setViewing(null)} /> : null}
      <Candidates person={person} />
    </section>
  );
}

/** 「可能是同一人？」 one pair at a time, the likeliest first: the same child is often split into several groups. */
function MergeSuggestion({ people, pair }: { people: PersonRow[]; pair: { a: string; b: string; score: number } }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const a = people.find((p) => p.id === pair.a);
  const b = people.find((p) => p.id === pair.b);
  const done = () => void qc.invalidateQueries({ queryKey: ["people"] });
  // the smaller group joins the larger one, so a name already given stays
  const [from, into] = (a?.asset_count ?? 0) >= (b?.asset_count ?? 0) ? [pair.b, pair.a] : [pair.a, pair.b];
  const merge = useMutation({ mutationFn: () => api.mergePeople(from, into), onSuccess: done });
  const no = useMutation({ mutationFn: () => api.dismissMerge(pair.a, pair.b), onSuccess: done });
  if (!a || !b) return null;
  const face = (p: PersonRow) => (p.cover?.bbox ? <FaceCrop assetId={p.cover.asset_id} ch={p.cover.ch} bbox={p.cover.bbox} /> : null);
  return (
    <section data-testid="merge-suggestion" aria-label={t("faces.sameQuestion")} className="merge-card">
      <div className="merge-faces">
        {face(a)}
        {face(b)}
      </div>
      <div className="space-y-2">
        <p className="font-semibold">{t("faces.sameQuestion")}</p>
        <p className="text-xs text-[var(--text-muted)]">
          {t("faces.sameCounts", { a: a.asset_count, b: b.asset_count })}
        </p>
        <div className="flex gap-2">
          <Button onClick={() => merge.mutate()}>{t("faces.sameYes")}</Button>
          <Button variant="ghost" onClick={() => no.mutate()}>{t("faces.sameNo")}</Button>
        </div>
      </div>
    </section>
  );
}

export function PeoplePage({ initial }: { initial?: string } = {}) {
  const { t } = useTranslation();
  const q = useQuery({ queryKey: ["people"], queryFn: api.people, refetchInterval: 5000 });
  const [open, setOpen] = useState<string | null>(initial ?? null);
  const selected = (q.data?.people ?? []).find((p) => p.id === open);
  return (
    <section className="space-y-4 p-6" aria-labelledby="people-h">
      <header className="flex items-center justify-between">
        <h1 id="people-h" className="text-xl font-semibold">{t("faces.people")}</h1>
        <Link to="/people/review" className="underline">{t("faces.review")}</Link>
      </header>
      {q.isSuccess && q.data.people.length === 0 ? <p className="text-[var(--text-muted)]">{t("faces.grouping")}</p> : null}
      {selected ? <PersonPanel key={selected.id} person={selected} onClose={() => setOpen(null)} /> : null}
      {!selected && q.data?.merge_suggestions?.[0] ? (
        <MergeSuggestion key={`${q.data.merge_suggestions[0].a}-${q.data.merge_suggestions[0].b}`} people={q.data.people} pair={q.data.merge_suggestions[0]} />
      ) : null}
      <ul className="grid grid-cols-[repeat(auto-fill,minmax(10rem,1fr))] gap-3">
        {(q.data?.people ?? []).map((p) => (
          <li key={p.id} data-testid="person">
            <button type="button" aria-pressed={open === p.id} onClick={() => setOpen(open === p.id ? null : p.id)} className="person-card">
              {p.cover?.bbox ? (
                <FaceCrop assetId={p.cover.asset_id} ch={p.cover.ch} bbox={p.cover.bbox} />
              ) : p.cover ? (
                <img src={mediaUrl(p.cover.asset_id, "t256", p.cover.ch)} alt="" loading="lazy" />
              ) : null}
              <span className="font-medium" translate="no">{p.name ?? t("faces.unnamed")}</span>
              <span className="text-xs text-[var(--text-muted)]">{t("faces.count", { n: p.asset_count })}</span>
            </button>
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
