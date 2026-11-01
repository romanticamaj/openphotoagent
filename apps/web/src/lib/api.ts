// Thin REST client for /api/v1 (replaced by the OpenAPI-generated ts-client in slice 9/10).
export class ApiError extends Error {
  constructor(public status: number, public body: unknown) {
    super(`HTTP ${status}`);
  }
}

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(`/api/v1${path}`, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) throw new ApiError(r.status, await r.json().catch(() => null));
  return (await r.json()) as T;
}

export type Root = { worker_id: string; alias: string; mode: "ro" | "rw" };
export type Job = { id: string; status: string; plan_id: string | null };
export type Plan = { id: string; status: string; summary: { copy: number; folders: number; total: number } };

export const api = {
  roots: () => req<Root[]>("GET", "/roots"),
  browse: (root: string, path: string) =>
    req<{ dirs: string[] }>("GET", `/fs/browse?root=${encodeURIComponent(root)}&path=${encodeURIComponent(path)}`),
  createSource: (root_alias: string, sub_path: string) => req<{ id: string }>("POST", "/sources", { root_alias, sub_path }),
  createJob: (source_id: string, preset: string, target: { root_alias: string; sub_path: string }) =>
    req<Job>("POST", "/jobs", { kind: "organize", source_id, preset, target }),
  job: (id: string) => req<Job>("GET", `/jobs/${id}`),
  plan: (id: string) => req<Plan>("GET", `/output-plans/${id}`),
  approve: (id: string) => req<Plan>("POST", `/output-plans/${id}/approve`, {}),
  undo: (id: string) => req<Plan>("POST", `/output-plans/${id}/undo`),
};
