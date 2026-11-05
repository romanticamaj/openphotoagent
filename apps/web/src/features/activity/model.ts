// Activity Center model (06 §6.1): indicator state and worker × lane grouping.
export type Op = {
  id: string;
  type: string;
  status: string;
  seq?: number;
  lane?: string;
  worker?: string | null;
  fraction?: number;
  unread?: boolean;
  error?: { code: string; fixes?: Array<{ id: string; type: string; primary?: boolean }> } | null;
  active_ms?: number;
  updated_at_ms?: number;
  server_now_ms?: number;
  eta?: { state?: string; p50_s?: number; since_s?: number } | null;
  waiting_reason?: string | null;
  [k: string]: unknown;
};

export type Indicator = { state: "idle" | "running" | "paused" | "failed" | "interrupted"; icon: string; token: string; labelKey: string };

const IND: Record<Indicator["state"], Omit<Indicator, "state" | "labelKey">> = {
  idle: { icon: "check", token: "--text-muted" },
  running: { icon: "spinner", token: "--info" },
  paused: { icon: "pause", token: "--warning" },
  failed: { icon: "alert", token: "--danger" },
  interrupted: { icon: "plug-off", token: "--warning" },
};

/** Precedence: interrupted > failed (unread) > paused > running > idle. */
export function indicatorState(ops: Op[]): Indicator {
  let state: Indicator["state"] = "idle";
  if (ops.some((o) => ["running", "queued", "waiting", "pausing", "cancelling"].includes(o.status))) state = "running";
  if (ops.some((o) => o.status === "paused")) state = "paused";
  if (ops.some((o) => o.status === "failed" && o.unread)) state = "failed";
  if (ops.some((o) => o.status === "interrupted")) state = "interrupted";
  return { state, ...IND[state], labelKey: `activity.indicator.${state}` };
}

export type Group = { key: string; ops: Op[] };

export function groupOps(ops: Op[]): Group[] {
  const groups = new Map<string, Op[]>();
  for (const o of ops) {
    const key = o.worker ? `${o.worker} · ${o.lane ?? "default"}` : (o.lane ?? "control");
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(o);
  }
  return [...groups].map(([key, list]) => ({ key, ops: list }));
}

/** API Operation row → view model (fraction from byte progress when the server sends only done/total). */
export function fromApi(o: { progress?: { done?: number; total?: number; fraction?: number } | null; [k: string]: unknown }): Op {
  const p = o.progress ?? {};
  const fraction = p.fraction ?? (p.total ? (p.done ?? 0) / p.total : 0);
  return { ...(o as unknown as Op), fraction };
}
