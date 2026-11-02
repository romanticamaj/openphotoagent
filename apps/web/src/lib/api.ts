import { endpoint, platform } from "./platform";
// Thin REST client for /api/v1 (replaced by the OpenAPI-generated ts-client in slice 9/10).
export class ApiError extends Error {
  constructor(public status: number, public body: unknown) {
    super(`HTTP ${status}`);
  }
}

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const { url, headers } = await endpoint(`/api/v1${path}`);
  const r = await fetch(url, {
    method,
    // only the app's own pages send this; a cross-site page cannot without a CORS preflight (MCP-APPR-002)
    headers: body === undefined ? { ...headers, "X-OPA-UI": "1" } : { ...headers, "X-OPA-UI": "1", "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) throw new ApiError(r.status, await r.json().catch(() => null));
  return (await r.json()) as T;
}

export type McpToken = {
  id: string;
  number: number;
  name: string;
  client: string | null;
  scopes: string[];
  folders: Array<{ source: string; path: string }>;
  revoked: boolean;
  calls: number;
  images_returned: number;
  last_used_at: string | null;
};
export type McpSettings = { allow_apply_requests: boolean; faces_enabled: boolean; media_policy: string; exif_free_text: boolean };
export type McpClientProposal = { client: string; path: string; exists: boolean; before: unknown; after: { command: string; args: string[] }; command_line: string | null };
export type Approval = {
  id: string;
  plan_id: string;
  kind: "apply" | "undo";
  status: string;
  client: string | null;
  grant: { id: string; name: string; number: number } | null;
  requested_at: string;
  expires_at: string;
  note: string | null;
  counts: Record<string, number>;
};
export type FaceSettings = { enabled: boolean; prompt_dismissed: boolean; workspace_name: string; worker_purge_pending: boolean; space_id: number | null };
export type PersonRow = { id: string; name: string | null; status: string; face_count: number; asset_count: number };
export type FaceSuggestion = { face_id: string; person_id: string; person: string | null; score: number | null; asset_id: string };
export type PurgeReport = { faces: number; embeddings: number; prototypes: number; persons: number; worker_pending: boolean };
export type TimelineAsset = { id: string; ch?: string | null; w: number | null; h: number | null; thumbhash: string | null; taken_at: string | null; flags: string[] };
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
export type Variant = "t256" | "t512" | "p1024";

/** A photo's derived image. Desktop: straight from the local cache through the asset protocol, keyed by the
 *  content hash `ch` (10 §5.4); web: the API endpoint. */
const knownHash = new Map<string, string>(); // ids seen with their hash (e.g. dragged examples carry only the id)

export function mediaUrl(id: string, variant: Variant, ch?: string | null): string {
  const p = platform();
  if (ch) knownHash.set(id, ch);
  else ch = knownHash.get(id);
  if (p.kind === "desktop" && ch && p.thumbsDir && p.assetUrl) {
    return p.assetUrl(`${p.thumbsDir}/${ch.slice(0, 2)}/${ch}.${variant}.webp`);
  }
  return `/api/v1/assets/${encodeURIComponent(id)}/media?variant=${variant}`;
}

export type Recommendation = {
  mode: "standard" | "fast" | "unsupported";
  hardware: { os: string; gpu: string | null; vram_gb: number | null; ram_gb: number };
  combo: { variants: string[]; packs: string[]; download_bytes: number; download_minutes: number; estimated: boolean };
  per_1000_minutes: [number, number] | null;
  licenses: string[];
  requires_acceptance: boolean;
  warnings: Array<{ code: string; params: Record<string, unknown>; fixes: Array<{ type: string; url?: string; pack?: string }> }>;
};
export type Operation = {
  id: string;
  type: string;
  status: string;
  subject: { id?: string; kind?: string } | null;
  progress: { done?: number; total?: number } | null;
  waiting_reason: string | null;
  worker_id?: string | null;
  error: { code: string; params?: Record<string, unknown>; fixes?: Array<{ id?: string; type: string; target?: string | null }> } | null;
};

export type SearchBody = {
  q?: string | null;
  plan?: boolean;
  filters?: { tags_all?: string[]; taken_from?: string; taken_to?: string; category?: string };
  limit?: number;
  cursor?: string | null;
  facets?: string[];
};
export type SearchHit = { asset_id: string; ch?: string | null; score: number; matched_by: string[]; category: string | null; tags: string[]; caption: string | null; taken_at: string | null };
export type Planner = {
  source: string;
  confidence: number;
  explain: string[];
  text: string;
  filters: { taken_at?: { from: string; to: string }; tags_any?: string[]; [k: string]: unknown };
  unapplied: string[];
};
export type SearchResult = {
  items: SearchHit[];
  facets: Record<string, Record<string, number>>;
  next_cursor: string | null;
  total: number;
  degraded: boolean;
  planner?: Planner;
};

export type VariantView = {
  id: string;
  key: string;
  runtime: string;
  size_bytes: number;
  size_estimated: boolean;
  vram_gb: { weights?: number; mmproj?: number; runtime_overhead?: number };
  ram_gb: number | null;
  compat: { level: string; reasons: Array<{ code: string; params?: Record<string, unknown> }> };
  installation: { id: string; status: string } | null;
  perf: { basis: string; prior: { items_per_s?: Record<string, [number, number]> } | null };
};
export type ModelView = {
  id: string;
  display_name: string;
  kind: string;
  capabilities: string[];
  license: { spdx: string };
  best_variant: string | null;
  variants: VariantView[];
};
export type Preflight = {
  ready: boolean;
  blockers: Array<{ code: string; params: Record<string, unknown>; fixes?: Array<{ id?: string; type: string }> }>;
  warnings: unknown[];
  models?: Array<{ role: string; key: string; compat: VariantView["compat"] }>;
  revalidate_on?: string[];
};

export type Root = { worker_id: string; alias: string; mode: "ro" | "rw" };
export type Job = {
  id: string;
  status: string;
  plan_id: string | null;
  params?: Record<string, unknown>;
  waiting_reason?: string | null;
  source_status?: string | null;
  reviews_pending?: number;
};
export type PlanAction = { id: string; asset_id: string; action: string; src_alias?: string; src_rel: string; dst_alias: string; dst_rel: string; status: string; conflict: string };
export type Plan = {
  id: string;
  job_id: string;
  status: string;
  summary: { copy: number; folders: number; total: number; move?: number; trash?: number };
  outcome: { done: number; renamed: number; same_skipped: number; skipped: number; failed: number } | null;
  actions?: PlanAction[];
};
export type PreviewItem = { asset_id: string; ch?: string | null; rel: string; category_key: string; decision: string; confidence: number; source: string; dst_rel: string };
export type Preview = {
  id: string;
  status: "running" | "ready";
  items: PreviewItem[];
  estimate: { total: number; distribution: Record<string, number>; time: { p50_s: number } } | null;
};
export type Category = { key: string; name: string; description: string; examples?: string[] };
export type Review = {
  id: string;
  asset_id: string;
  ch?: string | null;
  job_id: string;
  status: string;
  suggested: { category: string; confidence: number; candidates: Array<string | { id?: string; key?: string }> };
  final: { category: string } | null;
};

export type DuplicateMember = { asset_id: string; ch?: string | null; rel_path?: string | null; source_id?: string };
export type DuplicateGroup = {
  group_id: string;
  reason: "hash" | "near";
  members: DuplicateMember[];
  representative: { asset_id: string };
  reclaimable_bytes: number;
};

export type Capabilities = { deployment: string; single_user: boolean; features: Record<string, boolean> };

export const api = {
  faceSettings: () => req<FaceSettings>("GET", "/faces/settings"),
  enableFaces: (sources: string[] | null = null) => req<FaceSettings>("POST", "/faces/enable", { consent: true, consent_text_version: "faces-v1", sources }),
  dismissFacePrompt: () => req<FaceSettings>("POST", "/faces/prompt/dismiss"),
  disableFaces: (del: boolean, confirm: string) => req<{ purged: PurgeReport | null }>("POST", "/faces/disable", { delete: del, confirm }),
  people: () => req<{ people: PersonRow[]; others: number }>("GET", "/people"),
  namePerson: (id: string, name: string) => req<unknown>("POST", `/people/${encodeURIComponent(id)}/name`, { name }),
  faceReview: () => req<FaceSuggestion[]>("GET", "/faces/review"),
  confirmFace: (faceId: string, personId: string) => req<unknown>("POST", `/faces/${encodeURIComponent(faceId)}/confirm`, { person_id: personId }),
  rejectFace: (faceId: string, personId: string) => req<unknown>("POST", `/faces/${encodeURIComponent(faceId)}/reject`, { person_id: personId }),
  mcpTokens: () => req<McpToken[]>("GET", "/mcp/tokens"),
  revokeMcpToken: (id: string) => req<{ revoked: boolean }>("DELETE", `/mcp/tokens/${encodeURIComponent(id)}`),
  mcpSettings: () => req<McpSettings>("GET", "/mcp/settings"),
  putMcpSettings: (patch: Record<string, unknown>) => req<McpSettings>("PUT", "/mcp/settings", patch),
  connectMcpClient: (client: string, scopes: string[]) =>
    req<McpToken & { credential: { store: string; warning: string | null } }>("POST", `/mcp/clients/${client}/connect`, { scopes }),
  mcpClientProposal: (client: string) => req<McpClientProposal>("GET", `/mcp/clients/${client}/proposal`),
  installMcpClient: (client: string) => req<{ path: string; backup: string | null }>("POST", `/mcp/clients/${client}/install`),
  approvals: (status = "pending") => req<Approval[]>("GET", `/approvals?status=${status}`),
  approval: (id: string) => req<Approval>("GET", `/approvals/${encodeURIComponent(id)}`),
  decideApproval: (id: string, body: { decision: string; move_confirmed: boolean; confirm_text: string | null }) =>
    req<{ status: string; job_id?: string }>("POST", `/approvals/${encodeURIComponent(id)}/decision`, body),
  capabilities: () => req<Capabilities>("GET", "/capabilities"),
  roots: () => req<Root[]>("GET", "/roots"),
  browse: (root: string, path: string) =>
    req<{ dirs: string[] }>("GET", `/fs/browse?root=${encodeURIComponent(root)}&path=${encodeURIComponent(path)}`),
  createSource: (root_alias: string, sub_path: string) => req<{ id: string }>("POST", "/sources", { root_alias, sub_path }),
  createJob: (source_id: string, preset: string, target: { root_alias: string; sub_path: string }, extra: Record<string, unknown> = {}) =>
    req<Job>("POST", "/jobs", { kind: "organize", source_id, preset, target, ...extra }),
  cancelJob: (id: string) => req<Job>("POST", `/jobs/${id}/cancel`),
  reapply: (id: string, thresholds: Record<string, number>) => req<{ plan_id: string }>("POST", `/jobs/${id}/reapply`, { thresholds }),
  createPreview: (source_id: string, preset: string, categories?: Category[]) =>
    req<{ id: string }>("POST", "/previews", { source_id, preset, categories, k: 10 }),
  preview: (id: string) => req<Preview>("GET", `/previews/${id}`),
  rerender: (id: string, thresholds: Record<string, number>) => req<{ items: PreviewItem[] }>("POST", `/previews/${id}/rerender`, { thresholds }),
  reviews: (job: string, status = "pending") => req<{ count: number; items: Review[] }>("GET", `/reviews?job_id=${job}&status=${status}`),
  actReview: (id: string, action: "accept" | "correct" | "skip", category?: string) => req<unknown>("POST", `/reviews/${id}`, { action, category }),
  skipRest: (job: string) => req<unknown>("POST", `/jobs/${job}/reviews/skip-rest`),
  job: (id: string) => req<Job>("GET", `/jobs/${id}`),
  plan: (id: string) => req<Plan>("GET", `/output-plans/${id}`),
  approve: (id: string, body: { move_confirmed?: boolean; confirm_text?: string } = {}) => req<Plan>("POST", `/output-plans/${id}/approve`, body),
  undo: (id: string) => req<Plan>("POST", `/output-plans/${id}/undo`),
  duplicates: () => req<{ groups: DuplicateGroup[] }>("GET", "/duplicates"),
  cleanupPlan: (group_ids: string[]) => req<{ plan_id: string; trash: number }>("POST", "/duplicates/cleanup-plan", { group_ids }),
  recommendation: (worker_id: string) => req<Recommendation>("GET", `/onboarding/recommendation?worker_id=${encodeURIComponent(worker_id)}`),
  installCombo: (worker_id: string, variants: string[]) =>
    req<{ operations: string[]; blocked: Array<{ variant: string; code: string }> }>("POST", "/onboarding/install", { worker_id, variants }),
  operations: (params = "status=all") => req<Operation[]>("GET", `/operations?${params}`),
  preflight: (action: string, params: Record<string, unknown> = {}) =>
    req<{ blockers: Array<{ code: string; params: Record<string, unknown> }>; warnings: unknown[] }>("POST", "/preflight", { action, params }),
  sources: () => req<Array<{ id: string; root_alias: string; sub_path: string }>>("GET", "/sources"),
  scanSource: (id: string) => req<{ task_id: string }>("POST", `/sources/${encodeURIComponent(id)}/scan`),
  search: (body: SearchBody) => req<SearchResult>("POST", "/search", body),
  searchIndex: () => req<{ searchable: number; total: number }>("GET", "/search-index"),
  createSelection: (body: { query?: SearchBody; asset_ids?: string[] }) => req<{ id: string; count: number }>("POST", "/selections", body),
  selectionLabels: (id: string, body: { category?: string; tags?: string[] }) => req<{ updated: number }>("POST", `/selections/${id}/labels`, body),
  selectionOrganize: (id: string, target: { root_alias: string; sub_path: string }) =>
    req<{ plan_id: string; job_id: string }>("POST", `/selections/${id}/organize`, { target }),
  models: (worker_id: string) => req<{ items: ModelView[] }>("GET", `/models?worker_id=${encodeURIComponent(worker_id)}`),
  model: (id: string, worker_id: string) => req<ModelView>("GET", `/models/${encodeURIComponent(id)}?worker_id=${encodeURIComponent(worker_id)}`),
  preflightFull: (action: string, params: Record<string, unknown>) => req<Preflight>("POST", "/preflight", { action, params }),
  resumeOp: (id: string) => req<unknown>("POST", `/operations/${encodeURIComponent(id)}/resume`),
  cancelOp: (id: string) => req<unknown>("POST", `/operations/${encodeURIComponent(id)}/cancel`),
  cancelJobMode: (id: string, mode: "graceful" | "immediate", confirm = false) => req<Job>("POST", `/jobs/${id}/cancel`, { mode, confirm }),
  doctor: (worker_id: string) =>
    req<{ results: Array<{ check_id: string; status: string; code: string | null; params: Record<string, unknown>; fixes: Array<{ id?: string; type: string }> }> }>(
      "GET",
      `/system/doctor?worker_id=${encodeURIComponent(worker_id)}`,
    ),
  buckets: () => req<{ buckets: Array<{ month: string; count: number }> }>("GET", "/timeline/buckets"),
  bucket: (month: string) => req<{ month: string; assets: TimelineAsset[] }>("GET", `/timeline/buckets/${month}`),
  asset: (id: string) => req<AssetInfo>("GET", `/assets/${encodeURIComponent(id)}`),
  correct: (id: string, category: string) => req<unknown>("PATCH", `/assets/${encodeURIComponent(id)}/labels`, { category }),
  preset: (key: string, lang: string) =>
    req<{ key: string; categories: Category[] }>("GET", `/presets/${encodeURIComponent(key)}?lang=${encodeURIComponent(lang)}`),
};
