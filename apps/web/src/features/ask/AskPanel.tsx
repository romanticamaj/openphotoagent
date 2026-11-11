// 「AI 問照片」 side panel (10 §9.1, §9.3): ⌘J / the top-bar button. Each turn streams its steps with an estimate
// while running and the elapsed time after (invariant 9); the parsed conditions are chips the user can remove and
// re-run without re-parsing (ASK-SSE-004); cloud mode is always visible (ASK-PRV-001); a machine without a GPU
// gets two ways forward instead of an error (ASK-PRV-005). Plan cards come from the plan's own data (ASK-ACT-002).
import { Link } from "@tanstack/react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, mediaUrl } from "../../lib/api";
import { initialTurn, photoStatus, reduce, renderAnswer, seconds, streamAsk, type Step, type Turn } from "./stream";

const SLOW_OK = "opa.ask.slowLocal";

function slowAccepted(): boolean {
  try {
    return localStorage.getItem(SLOW_OK) === "1";
  } catch {
    return false;
  }
}

export function useAsk() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const abort = useRef<AbortController | null>(null);
  const busy = turns.at(-1)?.status === "streaming";
  const run = async (question: string, extra: Record<string, unknown> = {}) => {
    const idx = turns.length;
    setTurns((ts) => [...ts, initialTurn(question)]);
    abort.current = new AbortController();
    await streamAsk(
      { message: question, ...extra },
      (e) => setTurns((ts) => ts.map((t, i) => (i === idx ? reduce(t, e) : t))),
      abort.current.signal,
    ).catch(() => setTurns((ts) => ts.map((t, i) => (i === idx && t.status === "streaming" ? { ...t, status: "failed" } : t))));
  };
  const stop = () => {
    const id = turns.at(-1)?.turnId;
    if (id) void api.cancelAsk(id);
  };
  return { turns, busy, run, stop };
}

function StepRow({ s }: { s: Step }) {
  const { t } = useTranslation();
  const time = s.status === "running" ? (s.eta_ms ? t("ask.eta", { s: seconds(s.eta_ms) }) : "") : s.elapsed_ms !== undefined ? t("ask.elapsed", { s: seconds(s.elapsed_ms) }) : "";
  const mark = s.status === "running" ? "◐" : s.status === "ok" ? "✓" : "✕";
  return (
    <li data-testid="ask-step" className="flex items-baseline gap-2 text-sm">
      <span aria-hidden="true">{mark}</span>
      <span>{s.label}</span>
      {s.summary ? <span className="text-[var(--text-muted)]">{s.summary}</span> : null}
      <span className="tnum ml-auto text-xs text-[var(--text-muted)]">{time}</span>
    </li>
  );
}

export function chipLabel(t: (k: string, o?: Record<string, unknown>) => string, key: string, v: unknown, names: Record<string, string> = {}): string {
  if (key === "taken_at") {
    const r = (v ?? {}) as { from?: string; to?: string };
    // the last day in it, as the planner says it: the range's end is exclusive (ASK-DATE-002)
    const last = r.to ? new Date(Date.parse(`${r.to}T00:00:00Z`) - 86_400_000).toISOString().slice(0, 10) : "";
    return t("ask.chipDate", { from: r.from ?? "", to: last });
  }
  if (key === "tags_any" || key === "tags_all") return t("ask.chipTag", { tag: ((v as string[]) ?? []).map((x) => names[x] ?? x).join("、") });
  if (key === "persons_all" || key === "persons_any") return t("ask.chipPerson", { names: ((v as string[]) ?? []).map((x) => names[x] ?? "?").join("、") });
  return key;
}

const STRIP = 8;

function Answer({ text, onPhoto, hashes }: { text: string; onPhoto?: (id: string) => void; hashes?: Record<string, string | null> }) {
  const { t } = useTranslation();
  const parts = renderAnswer(text);
  const photos = parts.filter((p) => p.kind === "photo");
  return (
    <div data-testid="ask-answer" className="space-y-2 text-sm leading-relaxed">
      <p>
        {parts.map((p, i) => (p.kind === "bold" ? <strong key={i}>{p.text}</strong> : p.kind === "text" ? <span key={i}>{p.text}</span> : null))}
      </p>
      {photos.length > 0 && (
        <ul aria-label={t("ask.cited")} className="flex flex-wrap gap-2">
          {photos.map((p) =>
            p.kind === "photo" ? (
              <li key={p.id}>
                <button type="button" onClick={() => onPhoto?.(p.id)} aria-label={t("ask.openPhoto")} className="block overflow-hidden rounded-[var(--radius-sm)]">
                  <img src={mediaUrl(p.id, "t512", hashes?.[p.id])} alt="" width={72} height={72} className="h-[72px] w-[72px] object-cover" />
                </button>
              </li>
            ) : null,
          )}
        </ul>
      )}
    </div>
  );
}

export function TurnView({ turn, onRerun }: { turn: Turn; onRerun: (filters: Record<string, unknown>, text: string) => void }) {
  const { t } = useTranslation();
  const [edited, setEdited] = useState<Record<string, unknown> | null>(null);
  const filters = edited ?? turn.filters?.applied ?? {};
  return (
    <article className="space-y-2 border-b border-[var(--border)] pb-3">
      <p className="text-sm">
        <span className="font-semibold">{t("ask.you")}：</span>
        {turn.question}
      </p>
      <ul className="space-y-1">
        {turn.steps.map((s) => (
          <StepRow key={s.id} s={s} />
        ))}
      </ul>
      {turn.filters && Object.keys(turn.filters.applied).length > 0 ? (
        <div className="flex flex-wrap items-center gap-2">
          {Object.entries(filters).map(([k, v]) => (
            <button
              key={k}
              type="button"
              aria-label={t("ask.removeChip", { label: chipLabel(t, k, v, turn.filters?.names) })}
              onClick={() => setEdited(Object.fromEntries(Object.entries(filters).filter(([x]) => x !== k)))}
              className="rounded-full border border-[var(--border)] px-2 py-0.5 text-xs"
            >
              {chipLabel(t, k, v, turn.filters?.names)} ×
            </button>
          ))}
          {edited !== null ? (
            <button type="button" onClick={() => onRerun(edited, turn.filters?.text ?? "")} className="rounded-[var(--radius-sm)] bg-[var(--accent)] px-2 py-0.5 text-xs text-[var(--accent-fg)]">
              {t("ask.rerun")}
            </button>
          ) : null}
        </div>
      ) : null}
      {turn.photos && turn.photos.role === "candidates" ? (
        <p data-testid="ask-photos-total" className="text-sm text-[var(--text-muted)]">
          {t("ask.found", { n: turn.photos.total.toLocaleString() })}
        </p>
      ) : null}
      {turn.photos && turn.photos.role === "candidates" && turn.photos.items.length > 0 ? (
        // what the answer is about, next to it: a number alone is hard to trust (ASK-UI-010)
        <div className="space-y-1">
          <ul data-testid="ask-photos" aria-label={t("ask.cited")} className="grid grid-cols-4 gap-1">
            {turn.photos.items.slice(0, STRIP).map((id) => (
              <li key={id}>
                <img src={mediaUrl(id, "t256", turn.photos?.hashes?.[id])} alt="" loading="lazy" className="aspect-square w-full rounded-[var(--radius-sm)] object-cover" />
              </li>
            ))}
          </ul>
          <Link to="/search" search={{ q: turn.filters?.text || turn.question }} className="text-sm text-[var(--accent)] underline">
            {t("ask.seeAll", { n: turn.photos.total.toLocaleString() })}
          </Link>
        </div>
      ) : null}
      {turn.cards.map((c) => (
        <div key={c.payload.plan_id} data-testid="ask-plan-card" className="space-y-1 rounded-[var(--radius-md)] border border-[var(--border)] p-3 text-sm">
          <p className="flex justify-between font-semibold">
            <span>{t("ask.plan.title")}</span>
            <span className="text-[var(--warn,var(--accent))]">{t("ask.plan.pending")}</span>
          </p>
          <p>{t("ask.plan.summary", { action: c.payload.action ?? "", count: c.payload.count, target: JSON.stringify(c.payload.target ?? "") })}</p>
          <p className="text-xs text-[var(--text-muted)]">{t("ask.plan.note")}</p>
          <Link to="/plans/$planId" params={{ planId: c.payload.plan_id }} className="text-[var(--accent)] underline">
            {t("ask.plan.open")}
          </Link>
        </div>
      ))}
      {turn.text ? <Answer text={turn.text} hashes={turn.photos?.hashes} /> : null}
      {turn.status === "cancelled" ? <p className="text-xs text-[var(--text-muted)]">{t("ask.cancelled")}</p> : null}
      {turn.status === "failed" ? <p role="alert" className="text-xs">{t("ask.failed")}</p> : null}
    </article>
  );
}

export function AskPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const settings = useQuery({ queryKey: ["assistant-settings"], queryFn: api.assistantSettings, enabled: open });
  const sugg = useQuery({ queryKey: ["ask-suggestions"], queryFn: () => api.askSuggestions(), enabled: open });
  const { turns, busy, run, stop } = useAsk();
  const [q, setQ] = useState("");
  const [slow, setSlow] = useState(slowAccepted);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (open) input.current?.focus();
  }, [open]);
  // Esc closes the panel from anywhere inside it; the shell returns focus to whatever opened it (A11Y-011)
  useEffect(() => {
    if (!open) return;
    const on = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      if (document.querySelector('[data-testid="lightbox"]')) return; // the lightbox owns Esc while it is open
      e.preventDefault();
      if (busy) stop();
      onClose();
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [open, busy, stop, onClose]);
  if (!open) return null;
  const s = settings.data;
  const cloud = s?.provider === "openai_compat";
  const needsAdvice = s && !cloud && s.advice === "external_or_slow_local" && !slow;
  const ask = (text: string) => {
    if (!text.trim() || busy) return;
    setQ("");
    void run(text).then(() => qc.invalidateQueries({ queryKey: ["ask-suggestions"] }));
  };
  return (
    <aside aria-label={t("ask.title")} className="ask-panel flex flex-col gap-3 border-l border-[var(--border)] bg-[var(--surface-1,var(--bg))] p-4" data-testid="ask-panel">
      <header className="flex items-center gap-2">
        <h2 className="font-semibold">{t("ask.title")}</h2>
        {s ? (
          <span data-testid="ask-mode" className="rounded-full border border-[var(--border)] px-2 text-xs">
            {t(cloud ? "ask.mode.cloud" : "ask.mode.local")}
          </span>
        ) : null}
        <span className="flex-1" />
        <button type="button" onClick={onClose} className="px-2">
          {t("ask.close")}
        </button>
      </header>
      {cloud ? (
        <p data-testid="ask-cloud-warning" role="note" className="rounded-[var(--radius-sm)] border border-[var(--border)] p-2 text-xs">
          {t("ask.cloudWarning")}
        </p>
      ) : null}
      {needsAdvice ? (
        <div id="ask-cpu-advice" data-testid="ask-cpu-advice" className="space-y-2 rounded-[var(--radius-md)] border border-[var(--border)] p-3 text-sm">
          <p>{t("ask.cpuAdvice")}</p>
          <div className="flex flex-wrap gap-2">
            <Link to="/settings/assistant" className="rounded-[var(--radius-sm)] bg-[var(--accent)] px-2 py-1 text-[var(--accent-fg)]">
              {t("ask.cpuSettings")}
            </Link>
            <button
              type="button"
              onClick={() => {
                try {
                  localStorage.setItem(SLOW_OK, "1");
                } catch {
                  /* private mode: the choice lasts for this page only */
                }
                setSlow(true);
              }}
              className="rounded-[var(--radius-sm)] border border-[var(--border)] px-2 py-1"
            >
              {t("ask.cpuSlow")}
            </button>
          </div>
        </div>
      ) : null}
      <div className="flex-1 space-y-3 overflow-y-auto" aria-live="polite">
        {turns.map((turn, i) => (
          <TurnView key={i} turn={turn} onRerun={(filters, text) => void run(turn.question, { filters, text })} />
        ))}
        {turns.length === 0 && (sugg.data?.suggestions ?? []).length > 0 ? (
          <ul aria-label={t("ask.suggestions")} className="flex flex-wrap gap-2">
            {(sugg.data?.suggestions ?? []).map((x) => (
              <li key={x.text}>
                <button type="button" onClick={() => ask(x.text)} className="rounded-full border border-[var(--border)] px-3 py-1 text-sm">
                  {x.text}
                </button>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          ask(q);
        }}
        className="flex gap-2"
      >
        <input
          ref={input}
          aria-label={t("ask.input")}
          placeholder={t("ask.placeholder")}
          value={q}
          aria-describedby={needsAdvice ? "ask-cpu-advice" : undefined}
          onChange={(e) => setQ(e.target.value)}
          className="flex-1 rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--bg)] px-2 py-1"
        />
        {busy ? (
          <button type="button" onClick={stop} className="px-2">
            {t("ask.stop")}
          </button>
        ) : (
          <button type="submit" className="rounded-[var(--radius-sm)] bg-[var(--accent)] px-3 py-1 text-[var(--accent-fg)]">
            {t("ask.send")}
          </button>
        )}
      </form>
    </aside>
  );
}

/** Lightbox: ask about this one photo (10 §9.1). Metadata answers first; nothing is guessed. */
export function AskPhoto({ assetId }: { assetId: string }) {
  const { t, i18n } = useTranslation();
  const sugg = useQuery({ queryKey: ["ask-suggestions", assetId], queryFn: () => api.askSuggestions(assetId) });
  const { turns, busy, run } = useAsk();
  const [q, setQ] = useState("");
  const last = turns.at(-1);
  const go = (text: string) => {
    if (!text.trim() || busy) return;
    setQ("");
    void run(text, { context: { photo_ids: [assetId], scope: "photo" } }); // about this photo (ASK-ONE-004)
  };
  return (
    <section role="region" aria-label={t("ask.photo.title")} className="space-y-2">
      <h2 className="font-semibold">{t("ask.photo.title")}</h2>
      <ul className="flex flex-wrap gap-2">
        {(sugg.data?.suggestions ?? []).map((x) => (
          <li key={x.text}>
            <button type="button" onClick={() => go(x.text)} className="facet-chip text-xs">
              {x.text}
            </button>
          </li>
        ))}
      </ul>
      {/* a rounded field that says what it is for, with a send button: an empty box looked like a form (audit round 27) */}
      <form
        className="ask-photo-field"
        onSubmit={(e) => {
          e.preventDefault();
          go(q);
        }}
      >
        <input
          aria-label={t("ask.photo.input")}
          placeholder={`${t("ask.photo.input")}…`}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          className="min-w-0 flex-1 bg-transparent px-1 text-sm outline-none"
        />
        <button type="submit" aria-label={t("ask.send")} title={t("ask.send")} className="icon-button">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M5 12h14M13 6l6 6-6 6" />
          </svg>
        </button>
      </form>
      {photoStatus(last, i18n.language) ? (
        <p role="status" data-testid="ask-photo-status" className="text-sm text-[var(--text-muted)]">
          {photoStatus(last, i18n.language)}
        </p>
      ) : null}
      {last?.text ? <Answer text={last.text} /> : null}
    </section>
  );
}
