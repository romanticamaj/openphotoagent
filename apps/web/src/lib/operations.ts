export type OpEvent = { id: string; seq: number; [k: string]: unknown };
export class OperationStore {
  apply(_ev: OpEvent): boolean {
    return true;
  }
  get(_id: string): OpEvent | undefined {
    return undefined;
  }
}
export class ElapsedClock {
  sync(_s: { serverTime: number; localNow: number; activeMs: number; updatedAt: number; running: boolean }) {}
  elapsedMs(_localNow: number): number {
    return -1;
  }
}
