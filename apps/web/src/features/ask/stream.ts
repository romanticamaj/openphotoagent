// 「AI 問照片」 turn state (10 §8.7, §10.2): SSE parsing over fetch (POST bodies, so not EventSource) and a pure
// reducer from events to what the panel shows. The answer is rendered from a whitelist: text, bold, cited photos —
// never links or images, whatever the model wrote (10 §8.6, ASK-GRD-006).

export type SseEvent = { event: string; data: unknown };
export type Step = {
  id: string;
  kind: string;
  label: string;
  tool?: string;
  status: "running" | "ok" | "error";
  eta_ms?: number;
  elapsed_ms?: number;
  summary?: string;
};
export type PlanCard = { kind: "plan"; payload: { plan_id: string; action: string | null; count: number; target: unknown; needs_approval: boolean } };
export type Turn = {
  question: string;
  turnId: string | null;
  mode: "local" | "cloud" | null;
  intent: string | null;
  path: string | null;
  steps: Step[];
  filters: { applied: Record<string, unknown>; text: string; explain: string[]; names?: Record<string, string> } | null;
  photos: { total: number; items: string[]; role: string; hashes?: Record<string, string | null> } | null;
  text: string;
  cards: PlanCard[];
  notices: Array<{ code: string; text: string; level: string }>;
  status: "streaming" | "completed" | "cancelled" | "failed";
};

export function parseSse(rest: string, chunk: string): { events: SseEvent[]; rest: string } {
  const buf = (rest + chunk).replace(/\r\n/g, "\n");
  const blocks = buf.split("\n\n");
  const tail = blocks.pop() ?? "";
  const events: SseEvent[] = [];
  for (const b of blocks) {
    let event = "message";
    const data: string[] = [];
    for (const line of b.split("\n")) {
      if (line.startsWith("event: ")) event = line.slice(7);
      else if (line.startsWith("data: ")) data.push(line.slice(6));
    }
    if (data.length) events.push({ event, data: JSON.parse(data.join("\n")) });
  }
  return { events, rest: tail };
}

export const initialTurn = (question: string): Turn => ({
  question,
  turnId: null,
  mode: null,
  intent: null,
  path: null,
  steps: [],
  filters: null,
  photos: null,
  text: "",
  cards: [],
  notices: [],
  status: "streaming",
});

type D = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

export function reduce(s: Turn, e: SseEvent): Turn {
  const d = (e.data ?? {}) as D;
  switch (e.event) {
    case "turn.started":
      return { ...s, turnId: d.turn_id, mode: d.mode, intent: d.intent, path: d.path };
    case "step.started":
      return {
        ...s,
        steps: [...s.steps, { id: d.step_id, kind: d.kind, label: d.label, tool: d.tool, eta_ms: d.eta_ms, status: "running" }],
      };
    case "step.completed":
      return {
        ...s,
        steps: s.steps.map((x) => (x.id === d.step_id ? { ...x, status: d.status === "ok" ? "ok" : "error", elapsed_ms: d.elapsed_ms, summary: d.summary } : x)),
      };
    case "filters":
      return { ...s, filters: { applied: d.applied_filters ?? {}, text: d.text ?? "", explain: d.planner_explain ?? [], ...(d.tag_names ? { names: d.tag_names } : {}) } };
    case "photos":
      return {
        ...s,
        photos: {
          total: d.total ?? 0,
          items: (d.items ?? []).map((i: D) => i.id),
          role: d.role,
          ...((d.items ?? []).some((i: D) => i.ch) ? { hashes: Object.fromEntries((d.items ?? []).map((i: D) => [i.id, i.ch ?? null])) } : {}),
        },
      };
    case "token":
      return { ...s, text: s.text + String(d.text ?? "") };
    case "card":
      return d.kind === "plan" ? { ...s, cards: [...s.cards, { kind: "plan", payload: d.payload }] } : s;
    case "notice":
      return { ...s, notices: [...s.notices, { code: d.code, text: d.text, level: d.level }] };
    case "turn.completed":
      return { ...s, status: d.status === "cancelled" ? "cancelled" : d.status === "failed" ? "failed" : "completed" };
    case "error":
      return { ...s, status: "failed" };
    default:
      return s;
  }
}

export const seconds = (ms: number) => (ms / 1000).toFixed(1);

export type Part = { kind: "text" | "bold"; text: string } | { kind: "photo"; id: string };

export function renderAnswer(md: string): Part[] {
  const clean = md
    .replace(/!\[[^\]]*\]\([^)]*\)/g, "") // images: dropped
    .replace(/\[([^\]]+)\]\((?:[^)]*)\)/g, (_m, label: string) => label) // links: their text only
    .replace(/https?:\/\/\S+/g, "");
  const out: Part[] = [];
  const re = /\*\*([^*]+)\*\*|\[\[photo:([A-Za-z0-9_-]+)\]\]/g;
  let at = 0;
  for (const m of clean.matchAll(re)) {
    if (m.index! > at) out.push({ kind: "text", text: clean.slice(at, m.index) });
    out.push(m[1] !== undefined ? { kind: "bold", text: m[1] } : { kind: "photo", id: m[2]! });
    at = m.index! + m[0].length;
  }
  if (at < clean.length) out.push({ kind: "text", text: clean.slice(at) });
  return out.filter((p) => p.kind !== "text" || p.text.length > 0);
}

/** POST /ask and feed every SSE event to ``on`` as it arrives. */
export async function streamAsk(body: Record<string, unknown>, on: (e: SseEvent) => void, signal?: AbortSignal): Promise<void> {
  const { endpoint } = await import("../../lib/platform");
  const { url, headers } = await endpoint("/api/v1/ask");
  const r = await fetch(url, {
    method: "POST",
    headers: { ...headers, "X-OPA-UI": "1", "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!r.ok || !r.body) {
    on({ event: "error", data: { status: r.status } });
    return;
  }
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let rest = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    const out = parseSse(rest, dec.decode(value, { stream: true }));
    rest = out.rest;
    out.events.forEach(on);
  }
}
