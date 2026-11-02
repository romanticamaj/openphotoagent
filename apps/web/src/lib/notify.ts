export type OpStatusEvent = { id: string; type?: string; status: string };
export function notificationFor(_e: OpStatusEvent): { key: string; route: string } | null {
  return null;
}
