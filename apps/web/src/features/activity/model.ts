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

/** The name to show: every job is a "job.label" operation, but analysing a folder is not organizing it (OP-020). */
export function opTypeKey(op: Pick<Op, "type"> & { subject?: unknown }): string {
  const sub = op.subject as { job_kind?: string; job_status?: string; outcome?: string } | undefined;
  const kind = sub?.job_kind;
  // an organize plan says where it stands (ACT-017): a 依日期 preview waits for approval; 「整理 完成」 read as done
  if (op.type === "job.label" && kind !== "label" && sub?.outcome === "discarded") return "job.discarded";
  if (op.type === "job.label" && kind !== "label" && sub?.job_status === "awaiting_approval") return "job.planWaiting";
  if (op.type === "job.label" && kind === "label") return "job.analyze";
  // a preview plans and changes nothing: 「整理 完成」 read as if files had been organized (ACT-015)
  if (op.type === "job.label" && kind === "preview") return "job.preview";
  return op.type;
}

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
