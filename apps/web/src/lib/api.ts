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

export type TimelineAsset = { id: string; w: number | null; h: number | null; thumbhash: string | null; taken_at: string | null; flags: string[] };
export type Label = {
  result_id: string;
  category: string | null;
  confidence: number | null;
  decision: string;
  source: string;
  model_fingerprint: string;
  caption: string | null;
  tags: string[] | null;
};
export type AssetInfo = {
  id: string;
  width: number | null;
  height: number | null;
  taken_at: string | null;
  date_source: string | null;
  size_bytes: number;
  exif: { camera_make?: string; camera_model?: string; lens?: string; iso?: number; exposure_s?: number; f_number?: number; focal_mm?: number; gps?: { lat: number; lon: number } };
  labels: Label[];
  human_label: { category: string; source: string } | null;
  locations?: Array<{ source_id: string; root_alias: string; rel_path: string; missing: boolean }>;
};
export type Category = { key: string; name: string; description: string };
export type Variant = "t256" | "t512" | "p1024";

export const mediaUrl = (id: string, variant: Variant) => `/api/v1/assets/${encodeURIComponent(id)}/media?variant=${variant}`;

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
  buckets: () => req<{ buckets: Array<{ month: string; count: number }> }>("GET", "/timeline/buckets"),
  bucket: (month: string) => req<{ month: string; assets: TimelineAsset[] }>("GET", `/timeline/buckets/${month}`),
  asset: (id: string) => req<AssetInfo>("GET", `/assets/${encodeURIComponent(id)}`),
  correct: (id: string, category: string) => req<unknown>("PATCH", `/assets/${encodeURIComponent(id)}/labels`, { category }),
  preset: (key: string, lang: string) =>
    req<{ key: string; categories: Category[] }>("GET", `/presets/${encodeURIComponent(key)}?lang=${encodeURIComponent(lang)}`),
};
