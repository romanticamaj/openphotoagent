// Operation event handling on the client (06 §5.6, §5.7).
export type OpEvent = { id: string; seq: number; [k: string]: unknown };

/** Keeps the latest state per operation; events older than the known seq are dropped (OP-009). */
export class OperationStore {
  private ops = new Map<string, OpEvent>();
  apply(ev: OpEvent): boolean {
    const cur = this.ops.get(ev.id);
    if (cur && ev.seq <= cur.seq) return false;
    this.ops.set(ev.id, { ...cur, ...ev });
    return true;
  }
  get(id: string): OpEvent | undefined {
    return this.ops.get(id);
  }
}

/** Elapsed time accumulated locally every second, corrected by the server clock offset; never moves backwards (OP-014). */
export class ElapsedClock {
  private offset = 0;
  private activeMs = 0;
  private updatedAt = 0;
  private running = false;
  private shown = 0;
  sync(s: { serverTime: number; localNow: number; activeMs: number; updatedAt: number; running: boolean }) {
    this.offset = s.serverTime - s.localNow;
    this.activeMs = s.activeMs;
    this.updatedAt = s.updatedAt;
    this.running = s.running;
  }
  elapsedMs(localNow: number): number {
    const raw = this.activeMs + (this.running ? Math.max(0, localNow + this.offset - this.updatedAt) : 0);
    this.shown = Math.max(this.shown, raw);
    return this.shown;
  }
}
