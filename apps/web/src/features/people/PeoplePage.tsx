// People (07 §4): off until the person opts in (never blocking), a wall of named and unnamed people, a keyboard
// review loop 「這是不是 X？」 (least certain first) and Settings › 人臉 with consent and disable-with-delete.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useLocation, useNavigate } from "@tanstack/react-router";
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
// 「全部都是」 marks only clear, sure candidates; the rest are decided one at a time (ADR-0035, FACE-FIND-020)
const oneClick = (c: FaceCandidate) => c.clear !== false && c.sure !== false;
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
  const [at, setAt] = useState(0); // the tile the keys act on (FACE-UI-019)
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || e.metaKey || e.ctrlKey || e.altKey || !items.length) return;
      const k = e.key.toLowerCase();
      const cur = items[Math.min(at, items.length - 1)];
      if (k === "y" || k === "n") {
        setMarks((x) => ({ ...x, [cur.face_id]: k === "y" ? "yes" : "no" }));
        setAt((i) => Math.min(i + 1, items.length - 1));
      } else if (k === "s" || e.key === "ArrowRight") setAt((i) => Math.min(i + 1, items.length - 1));
      else if (e.key === "ArrowLeft") setAt((i) => Math.max(i - 1, 0));
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  });
  const pick = (m: Mark) => Object.keys(marks).filter((k) => marks[k] === m);
  const yes = pick("yes");
  const no = pick("no");
  const [done, setDone] = useState<{ yes: number; no: number } | null>(null);
  const refresh = () => {
    for (const k of ["person-candidates", "person-photos"]) void qc.invalidateQueries({ queryKey: [k, person.id] });
    void qc.invalidateQueries({ queryKey: ["people"] });
  };
  const send = useMutation({
    mutationFn: () => api.reviewPerson(person.id, { yes, no }),
    onSuccess: (r) => {
      setMarks({});
      setShown(PAGE);
      setDone({ yes: r.confirmed, no: r.rejected });
      refresh();
    },
  });
  const undo = useMutation({
    mutationFn: () => api.undoReview(person.id),
    onSuccess: () => {
      setDone(null);
      refresh();
    },
  });
  const who = person.name ?? t("faces.thisPerson");
  // after 「送出」: what was sent, and a way back (FACE-FIND-016)
  const doneNote = done ? (
    <p data-testid="review-done" role="status" className="flex flex-wrap items-center gap-2 text-sm">
      {t("faces.reviewSent", { yes: done.yes, no: done.no })}
      <button type="button" className="underline" onClick={() => undo.mutate()}>
        {t("faces.undoReview")}
      </button>
    </p>
  ) : null;
  if (q.isSuccess && all.length === 0) return doneNote ?? <p className="text-sm text-[var(--text-muted)]">{t("faces.noCandidates")}</p>;
  if (!items.length) return doneNote;
  return (
    <section aria-labelledby={`cand-${person.id}`} className="space-y-2">
      {doneNote}
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 id={`cand-${person.id}`} className="font-semibold">{t("faces.candidatesTitle", { name: who, n: all.length })}</h3>
        <p className="text-xs text-[var(--text-muted)]">{t("faces.candidatesHint")}</p>
        <p data-testid="candidate-keys" className="w-full text-xs text-[var(--text-muted)]">
          {t("faces.candidateKeys")}
        </p>
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
                data-current={items.indexOf(c) === at ? "true" : undefined}
                data-score={c.score}
                data-tier={c.tier}
                aria-label={t(`faces.mark.${m}`)}
                title={t("faces.similarity", { n: Math.round(c.score * 100) })}
                className="candidate"
                onClick={() => setMarks((x) => ({ ...x, [c.face_id]: NEXT[m] }))}
              >
                <FaceCrop assetId={c.asset_id} ch={c.ch} bbox={c.bbox} />
                {c.clear === false ? (
                  <span className="candidate-hard">{t("faces.hardToJudge")}</span>
                ) : c.faint ? (
                  <span className="candidate-hard candidate-faint">{t("faces.faint")}</span>
                ) : null}
                {m !== "none" ? <span aria-hidden="true" className="candidate-mark">{m === "yes" ? "✓" : "✕"}</span> : null}
              </button>
            </li>
          );
        })}
      </ul>
      <div className="flex flex-wrap items-center gap-2">
        {/* only the clear ones: a face too small or soft to judge is never a one-click yes (ADR-0035) */}
        {items.some(oneClick) ? (
          // nothing to mark at once when no face is clear and likely (FACE-FIND-029)
          <Button
            variant="ghost"
            onClick={() => setMarks((x) => ({ ...x, ...Object.fromEntries(items.filter(oneClick).map((c) => [c.face_id, "yes" as Mark])) }))}
          >
            {t("faces.allYes")}
          </Button>
        ) : null}
        {items.some((c) => !oneClick(c)) ? (
          <span data-testid="all-yes-hint" className="text-xs text-[var(--text-muted)]">
            {t("faces.allYesHint", { n: items.filter((c) => !oneClick(c)).length })}
          </span>
        ) : null}
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
const LAST_PERSON = "opa.people.last";
const PERSON_MAX = 2000; // photos on a person's page; past this, search by their name

function PersonPanel({ person, onClose }: { person: PersonRow; onClose: () => void }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [name, setName] = useState(person.name ?? "");
  // every photo of the person, page after page: the page is where people look for 「all of them」 (FACE-UI-023)
  const photos = useQuery({
    queryKey: ["person-photos", person.id],
    queryFn: async () => {
      const body = { q: null, filters: { persons_any: [person.id] }, limit: 200 };
      const first = await api.search(body);
      let items = first.items;
      let next = first.next_cursor;
      while (next && items.length < PERSON_MAX) {
        const more = await api.search({ ...body, cursor: next });
        items = [...items, ...more.items];
        next = more.next_cursor;
      }
      return { ...first, items, next_cursor: next };
    },
  });
  // the photos the group holds, then the ones its likely faces are in (FACE-UI-020): most of a child's faces are in
  // no group yet, and the person page is where people look for 「all of them」
  const cands = useQuery({ queryKey: ["person-candidates", person.id], queryFn: () => api.personCandidates(person.id) });
  const shown = useMemo(() => {
    const sure = (photos.data?.items ?? []).map((h) => ({ id: h.asset_id, ch: h.ch, caption: h.caption, taken_at: h.taken_at, maybe: false }));
    const seen = new Set(sure.map((x) => x.id));
    const maybe = (cands.data?.items ?? [])
      .filter((c) => oneClick(c) && !seen.has(c.asset_id) && Boolean(seen.add(c.asset_id))) // only the sure ones (FACE-UI-021)
      .map((c) => ({ id: c.asset_id, ch: c.ch, caption: null as string | null, taken_at: null as string | null, maybe: true }));
    return [...sure, ...maybe];
  }, [photos.data, cands.data]);
  const nMaybe = shown.filter((x) => x.maybe).length;
  const [viewing, setViewing] = useState<number | null>(null);
  const grid: GridApi = useMemo(
    () => ({
      total: shown.length,
      ordinal: (p) => p.index,
      at: async (i) => {
        const h = shown[i];
        if (!h) return null;
        return { asset: { id: h.id, ch: h.ch, w: null, h: null, thumbhash: null, taken_at: h.taken_at, flags: [] }, pos: { bucket: 0, index: i } };
      },
    }),
    [shown],
  );
  const [renaming, setRenaming] = useState(false);
  const save = useMutation({
    mutationFn: () => api.namePerson(person.id, name.trim()),
    onSuccess: () => {
      setRenaming(false);
      void qc.invalidateQueries({ queryKey: ["people"] });
    },
  });
  // the span of their photos, for the header (FACE-UI-025)
  const days = (photos.data?.items ?? []).map((h) => h.taken_at?.slice(0, 10)).filter((d): d is string => Boolean(d)).sort();
  const span = days.length ? (days[0] === days[days.length - 1] ? days[0] : `${days[0]} – ${days[days.length - 1]}`) : null;
  return (
    <section aria-label={person.name ?? t("faces.unnamed")} className="space-y-3 rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--surface)] p-4">
      <div data-testid="person-header" className="person-header">
        {person.cover?.bbox ? (
          <FaceCrop assetId={person.cover.asset_id} ch={person.cover.ch} bbox={person.cover.bbox} />
        ) : person.cover ? (
          <img src={mediaUrl(person.cover.asset_id, "t256", person.cover.ch)} alt="" className="person-header-face" />
        ) : null}
        <div className="min-w-0 flex-1 space-y-1">
      {person.name && !renaming ? (
        // a named person: their name is the heading; renaming is a deliberate step (FACE-UI-022)
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="text-lg font-semibold" translate="no">
            {person.name}
          </h2>
          <button type="button" className="text-sm underline" onClick={() => setRenaming(true)}>
            {t("faces.rename")}
          </button>
          <button type="button" className="ml-auto text-sm underline" onClick={onClose}>{t("dock.close")}</button>
        </div>
      ) : (
      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <label className="flex flex-col gap-1 text-sm">
          {person.name ? t("lightbox.faceName") : t("faces.whoIsThis")}
          <input value={name} onChange={(e) => setName(e.target.value)} className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--bg)] px-2 py-1" />
        </label>
        <Button type="submit" disabledReason={name.trim() ? null : { code: "NAME_REQUIRED", message: t("faces.nameFirst") }}>
          {t("faces.saveName")}
        </Button>
        <button type="button" className="ml-auto text-sm underline" onClick={onClose}>{t("dock.close")}</button>
      </form>
      )}
          <p className="tnum text-sm text-[var(--text-muted)]">
            {t("faces.count", { n: person.asset_count })}
            {span ? ` · ${span}` : ""}
          </p>
        </div>
      </div>
      <h3 data-testid="person-photos-title" className="font-semibold">
        {nMaybe
          ? t("faces.photosWithMaybe", { n: shown.length, sure: shown.length - nMaybe, maybe: nMaybe })
          : t("faces.photos", { n: photos.data?.total ?? 0 })}
      </h3>
      <div className="explore-grid photo-wall">
        {shown.map((h, i) => (
          <button
            key={h.id}
            type="button"
            data-testid="person-photo"
            data-maybe={h.maybe ? "true" : undefined}
            className="explore-tile"
            aria-label={h.maybe ? t("faces.maybePhoto") : (h.caption ?? t("faces.openPhoto"))}
            onClick={() => setViewing(i)}
          >
            <img src={mediaUrl(h.id, "t256", h.ch)} alt="" loading="lazy" />
            {h.maybe ? <span className="photo-maybe">{t("faces.maybe")}</span> : null}
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
function MergeSuggestion({ people, pair, onSkip }: { people: PersonRow[]; pair: { a: string; b: string; score: number }; onSkip: () => void }) {
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
        <div data-testid="merge-more" className="merge-more">
          <MoreFaces person={a} skip={a.cover?.asset_id} />
          <MoreFaces person={b} skip={b.cover?.asset_id} />
        </div>
        <div className="flex gap-2">
          <Button onClick={() => merge.mutate()}>{t("faces.sameYes")}</Button>
          <Button variant="ghost" onClick={() => no.mutate()}>{t("faces.sameNo")}</Button>
          <button type="button" className="text-sm underline" onClick={onSkip}>{t("faces.sameLater")}</button>
        </div>
      </div>
    </section>
  );
}

const MORE = 5;

/** A few more faces of one group, from different photos and clearest first: one cover is too little to decide on
 * (FACE-MGT-013). */
function MoreFaces({ person, skip }: { person: PersonRow; skip?: string }) {
  const { t } = useTranslation();
  const q = useQuery({ queryKey: ["person", person.id], queryFn: () => api.person(person.id), staleTime: 60_000 });
  const seen = new Set(skip ? [skip] : []);
  const picks = [...(q.data?.faces ?? [])]
    .sort((x, y) => y.tier - x.tier)
    .filter((f) => !seen.has(f.asset_id) && Boolean(seen.add(f.asset_id)))
    .slice(0, MORE);
  if (!picks.length) return null;
  const name = person.name ?? t("faces.unnamed");
  return (
    <div className="merge-more-group">
      <p data-testid="merge-row-label" className="text-xs text-[var(--text-muted)]">
        {t("faces.groupLabel", { name, n: person.asset_count })}
      </p>
      <ul className="merge-more-row" aria-label={t("faces.moreOf", { name })}>
        {picks.map((f) => (
          <li key={f.id}>
            <FaceCrop assetId={f.asset_id} ch={f.ch} bbox={f.bbox} />
          </li>
        ))}
      </ul>
    </div>
  );
}

export function PeoplePage({ initial }: { initial?: string } = {}) {
  const { t } = useTranslation();
  const nav = useNavigate();
  const q = useQuery({ queryKey: ["people"], queryFn: api.people, refetchInterval: 5000 });
  const [open, setOpen] = useState<string | null>(initial ?? null);
  useEffect(() => setOpen(initial ?? null), [initial]);
  const selected = (q.data?.people ?? []).find((p) => p.id === open);
  // a person is a page of their own (FACE-UI-023): their photos opened above the wall, under the other people
  const openPerson = (id: string) => {
    try {
      window.sessionStorage.setItem(LAST_PERSON, id); // where to come back to, by 「← 人物」 or the browser's back
    } catch {
      /* not remembered: the wall opens at its top */
    }
    setOpen(id);
    void nav({ to: "/people/$personId", params: { personId: id } });
  };
  const back = () => {
    setOpen(null);
    void nav({ to: "/people", search: { person: undefined } });
  };
  // the first pair whose two groups are on the wall (small groups are folded away)
  const onWall = new Set((q.data?.people ?? []).map((p) => p.id));
  const [later, setLater] = useState<Set<string>>(() => new Set()); // 「稍後再說」: this visit only, nothing written
  const pair = (q.data?.merge_suggestions ?? []).find((x) => onWall.has(x.a) && onWall.has(x.b) && !later.has(`${x.a}|${x.b}`));
  // back on the wall: the person you came from, in view and focused (FACE-UI-024: it jumped to the top)
  const wallReady = !selected && q.isSuccess;
  useEffect(() => {
    if (!wallReady) return;
    let id: string | null = null;
    try {
      id = window.sessionStorage.getItem(LAST_PERSON);
    } catch {
      return;
    }
    // kept a moment: 「← 人物」 shows the wall here and then the route remounts the page, which must find it too
    // (audit round 63: the browser's back focused the card, 「← 人物」 left focus on the page)
    const forget = window.setTimeout(() => {
      try {
        window.sessionStorage.removeItem(LAST_PERSON);
      } catch {
        /* nothing to forget */
      }
    }, 1500);
    const card = id ? document.querySelector<HTMLElement>(`[data-person-id="${CSS.escape(id)}"] button`) : null;
    card?.scrollIntoView?.({ block: "center" });
    card?.focus();
    return () => window.clearTimeout(forget);
  }, [wallReady]);
  if (selected) {
    return (
      <section className="space-y-4 p-6" aria-label={selected.name ?? t("faces.unnamed")}>
        <button type="button" className="text-sm text-[var(--text-muted)] underline" onClick={back}>
          {t("faces.backToPeople")}
        </button>
        <PersonPanel key={selected.id} person={selected} onClose={back} />
      </section>
    );
  }
  return (
    <section className="space-y-4 p-6" aria-labelledby="people-h">
      <header className="flex items-center justify-between">
        <h1 id="people-h" className="text-xl font-semibold">{t("faces.people")}</h1>
        <Link to="/people/review" className="underline">{t("faces.review")}</Link>
      </header>
      {q.isSuccess && q.data.people.length === 0 ? <p className="text-[var(--text-muted)]">{t("faces.grouping")}</p> : null}
      {pair ? (
        <MergeSuggestion
          key={`${pair.a}-${pair.b}`}
          people={q.data!.people}
          pair={pair}
          onSkip={() => setLater((x) => new Set(x).add(`${pair.a}|${pair.b}`))}
        />
      ) : null}
      <ul className="grid grid-cols-[repeat(auto-fill,minmax(10rem,1fr))] gap-3">
        {(q.data?.people ?? []).map((p) => (
          <li key={p.id} data-testid="person" data-person-id={p.id}>
            <button type="button" onClick={() => openPerson(p.id)} className="person-card">
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
