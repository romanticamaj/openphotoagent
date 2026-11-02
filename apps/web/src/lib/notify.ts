// OS notifications for finished long work (08 §6, DESK-022). The desktop shell provides `platform().notify`;
// the web build shows none (the Activity Center already lists everything).
export type OpStatusEvent = { id: string; type?: string; status: string };

const DONE: Record<string, { key: string; route: string }> = {
  "job.label": { key: "notify.labelDone", route: "/organize" },
  "output.apply": { key: "notify.applyDone", route: "/organize" },
  "model.download": { key: "notify.modelReady", route: "/models" },
};

export function notificationFor(e: OpStatusEvent): { key: string; route: string } | null {
  if (!e.type || !(e.type in DONE)) return null;
  if (e.status === "succeeded") return DONE[e.type];
  if (e.status === "failed") return { key: "notify.failed", route: "/" };
  return null;
}
