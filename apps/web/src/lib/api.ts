import { endpoint, platform } from "./platform";
// Thin REST client for /api/v1 (replaced by the OpenAPI-generated ts-client in slice 9/10).
export type AnalysisStatus = {
  jobs: Array<{ job_id: string; folder: string; state: "queued" | "scanning" | "analysing"; done: number; total: number; failed: number }>;
  current: Array<{ asset_id: string; ch: string | null; rel: string }>;
  recent: Array<{ asset_id: string; ch: string | null; rel: string; caption: string | null; category: string | null; category_name: string | null }>;
};

export type FolderPreview = {
  photos: number;
  videos: number;
  others: number;
  bytes: number;
  truncated: boolean;
  subfolders: Array<{ name: string; photos: number; videos: number }>;
  samples: string[];
  taken_from: string | null;
  taken_to: string | null;
};

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
  if (!r.ok) {
    const problem = await r.json().catch(() => null);
    // control plane: the session ended → sign in again and come back (02 §6.3)
    if (r.status === 401 && (problem as { code?: string } | null)?.code === "AUTH_REQUIRED" && platform().kind === "web") {
      const here = window.location.pathname + window.location.search;
      if (!window.location.pathname.startsWith("/login")) window.location.assign(`/login?next=${encodeURIComponent(here)}`);
    }
    throw new ApiError(r.status, problem);
  }
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
export type McpSettings = { enabled: boolean; allow_apply_requests: boolean; faces_enabled: boolean; media_policy: string; exif_free_text: boolean };
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
export type PersonRow = { id: string; name: string | null; status: string; face_count: number; asset_count: number; cover?: { asset_id: string; ch: string | null; bbox?: number[] } | null };
export type FaceCandidate = { face_id: string; asset_id: string; ch: string | null; bbox: number[]; score: number; tier?: number; clear?: boolean; sure?: boolean; faint?: boolean; group: string | null };
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
  tag_names?: Record<string, string>;
};
export type Album = { id: string; name: string; kind: "favorites" | "album"; count: number; cover: { asset_id: string; ch: string | null } | null };
export type AlbumDetail = { id: string; name: string; kind: "favorites" | "album"; photos: Array<{ asset_id: string; ch: string | null; taken_at: string | null }> };
export type AssetInfo = {
  /** ★ and the albums it is in (ADR-0041) */
  favorite?: boolean;
  albums?: Array<{ id: string; name: string }>;
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
  people?: Array<{ id: string; name: string | null }>;
  people_faces?: Record<string, { bbox: number[]; ch: string | null }>;
  faces_state?: "off" | "folder_off" | "on";
  faces_found?: { total: number; small: number };
  unknown_faces?: Array<{ id?: string; bbox: number[]; small: boolean; ch?: string | null }>;
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
  combo: { variants: string[]; packs: string[]; runtime?: string; download_bytes: number; download_minutes: number; estimated: boolean };
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
  soft_only?: boolean;
  filters?: { tags_all?: string[]; taken_from?: string; taken_to?: string; category?: string; persons_any?: string[] };
  limit?: number;
  cursor?: string | null;
  facets?: string[];
  lang?: string;
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
  tag_names?: Record<string, string>;
  next_cursor: string | null;
  total: number;
  total_capped?: boolean;
  /** near misses, shown apart: outside `total`, facets and selections (ADR-0027) */
  related?: { asset_id: string; ch: string | null; caption: string | null }[];
  relaxed?: string[];
  /** expressions that rank without filtering, and how many photos show them (SRCH-CNC-007) */
  soft?: { word: string; key: string; shown: number; implied_by?: string }[];
  /** weak look-alikes, last: how many and from where (ADR-0040) */
  weak?: number;
  weak_from?: number | null;
  degraded: string[];
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
export type LibrarySource = { id: string; root_alias: string; sub_path: string; photos: number; analysed: number; tree_photos?: number; tree_analysed?: number };
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
  progress?: { done: number; total: number; elapsed_s: number };
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
  reason?: string | null;
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

export type Capabilities = {
  deployment: string;
  single_user: boolean;
  login?: { password: boolean; oidc: boolean };
  features: Record<string, boolean>;
};
export type Me = { id: string; email: string; roles: Record<string, string>; csrf: string };
export type Workspace = { id: string; name: string };

export type AssistantSettings = {
  cloud_people?: boolean;
  provider: "local" | "openai_compat";
  endpoint_url: string | null;
  media_policy: string;
  retention_days: number;
  local_capable: boolean;
  advice: "external_or_slow_local" | null;
  loop_enabled: boolean;
};
export type AskSuggestion = { kind: string; text: string };

export type CaptionIndexState = {
  active: { id: number; model_key: string; count: number } | null;
  building: { id: number; model_key: string; done: number; total: number; ready: boolean; operation_id: string | null } | null;
};

/** The vector paths that could not answer; empty when search ran in full (05 §2.6). */
export const isDegraded = (r?: { degraded: string[] }) => (r?.degraded.length ?? 0) > 0;

export const api = {
  captionIndex: () => req<CaptionIndexState>("GET", "/search/caption-index"),
  buildCaptionIndex: (model_key: string) => req<unknown>("POST", "/search/caption-index", { model_key }),
  switchCaptionIndex: () => req<CaptionIndexState>("POST", "/search/caption-index/switch"),
  assistantSettings: () => req<AssistantSettings>("GET", "/assistant-settings"),
  putAssistantSettings: (patch: Partial<AssistantSettings>) => req<AssistantSettings>("PATCH", "/assistant-settings", patch),
  askSuggestions: (photoId?: string) =>
    req<{ suggestions: AskSuggestion[] }>("GET", `/ask/suggestions${photoId ? `?photo_id=${encodeURIComponent(photoId)}` : ""}`),
  cancelAsk: (turnId: string) => req<unknown>("POST", `/ask/turns/${encodeURIComponent(turnId)}/cancel`),
  faceSettings: () => req<FaceSettings>("GET", "/faces/settings"),
  enableFaces: (sources: string[] | null = null) => req<FaceSettings>("POST", "/faces/enable", { consent: true, consent_text_version: "faces-v1", sources }),
  dismissFacePrompt: () => req<FaceSettings>("POST", "/faces/prompt/dismiss"),
  disableFaces: (del: boolean, confirm: string) => req<{ purged: PurgeReport | null }>("POST", "/faces/disable", { delete: del, confirm }),
  people: () => req<{ people: PersonRow[]; others: number; merge_suggestions?: Array<{ a: string; b: string; score: number }> }>("GET", "/people"),
  person: (pid: string) =>
    req<PersonRow & { faces: Array<{ id: string; asset_id: string; ch: string | null; state: string; tier: number; bbox: number[] }> }>(
      "GET",
      `/people/${encodeURIComponent(pid)}`,
    ),
  basemap: () => req<{ region: string; available: boolean; bytes: number; operation_id: string | null }>("GET", "/map/basemap"),
  downloadBasemap: () => req<{ operation_id: string }>("POST", "/map/basemap/download", {}),
  nameFace: (fid: string, name: string) =>
    req<{ person_id: string; name: string }>("POST", `/faces/${encodeURIComponent(fid)}/name`, { name }),
  undoReview: (pid: string) => req<{ restored: number }>("POST", `/people/${encodeURIComponent(pid)}/review/undo`, {}),
  mergePeople: (from: string, into: string) => req<{ undo_token: string; moved: number }>("POST", `/people/${encodeURIComponent(from)}/merge`, { into }),
  dismissMerge: (a: string, b: string) => req<unknown>("POST", "/people/merge-suggestions/dismiss", { a, b }),
  namePerson: (id: string, name: string) => req<unknown>("POST", `/people/${encodeURIComponent(id)}/name`, { name }),
  personCandidates: (id: string) => req<{ items: FaceCandidate[]; references: number }>("GET", `/people/${encodeURIComponent(id)}/candidates`),
  reviewPerson: (id: string, body: { yes: string[]; no: string[] }) =>
    req<{ confirmed: number; rejected: number }>("POST", `/people/${encodeURIComponent(id)}/review`, body),
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
  login: (email: string, password: string) => req<{ ok: boolean; csrf: string }>("POST", "/auth/login", { email, password }),
  logout: () => req<{ ok: boolean }>("POST", "/auth/logout"),
  me: () => req<Me>("GET", "/auth/me"),
  workspaces: () => req<Workspace[]>("GET", "/workspaces"),
  roots: () => req<Root[]>("GET", "/roots"),
  browse: (root: string, path: string) =>
    req<{ dirs: string[] }>("GET", `/fs/browse?root=${encodeURIComponent(root)}&path=${encodeURIComponent(path)}`),
  folderPreview: (root: string, path: string) =>
    req<FolderPreview>("GET", `/fs/preview?root=${encodeURIComponent(root)}&path=${encodeURIComponent(path)}`),
  folderThumb: (root: string, path: string) =>
    req<{ thumbnail_b64: string }>("GET", `/fs/thumb?root=${encodeURIComponent(root)}&path=${encodeURIComponent(path)}`),
  albums: () => req<{ albums: Album[] }>("GET", "/albums"),
  album: (id: string) => req<AlbumDetail>("GET", `/albums/${encodeURIComponent(id)}`),
  createAlbum: (name: string) => req<{ id: string; name: string }>("POST", "/albums", { name }),
  renameAlbum: (id: string, name: string) => req<{ id: string; name: string }>("PATCH", `/albums/${encodeURIComponent(id)}`, { name }),
  deleteAlbum: (id: string) => req<unknown>("DELETE", `/albums/${encodeURIComponent(id)}`),
  addToAlbum: (id: string, asset_ids: string[]) => req<{ count: number }>("POST", `/albums/${encodeURIComponent(id)}/photos`, { asset_ids }),
  removeFromAlbum: (id: string, assetId: string) => req<{ count: number }>("DELETE", `/albums/${encodeURIComponent(id)}/photos/${encodeURIComponent(assetId)}`),
  setFavorite: (assetId: string, on: boolean) => req<{ favorite: boolean }>(on ? "PUT" : "DELETE", `/assets/${encodeURIComponent(assetId)}/favorite`),
  analysisStatus: (lang: string) => req<AnalysisStatus>("GET", `/analysis/status?lang=${encodeURIComponent(lang)}`),
  analyzeSource: (id: string) => req<{ job_id: string }>("POST", `/sources/${encodeURIComponent(id)}/analyze`),
  createSource: (root_alias: string, sub_path: string) => req<{ id: string }>("POST", "/sources", { root_alias, sub_path }),
  createJob: (source_id: string, preset: string, target: { root_alias: string; sub_path: string }, extra: Record<string, unknown> = {}) =>
    req<Job>("POST", "/jobs", { kind: "organize", source_id, preset, target, ...extra }),
  cancelJob: (id: string) => req<Job>("POST", `/jobs/${id}/cancel`),
  reapply: (id: string, thresholds: Record<string, number>) => req<{ plan_id: string }>("POST", `/jobs/${id}/reapply`, { thresholds }),
  createPreview: (source_id: string, preset: string, categories?: Category[]) =>
    req<{ id: string }>("POST", "/previews", { source_id, preset, categories, k: 10 }),
  preview: (id: string) => req<Preview>("GET", `/previews/${id}`),
  rerender: (id: string, thresholds: Record<string, number>) => req<{ items: PreviewItem[] }>("POST", `/previews/${id}/rerender`, { thresholds }),
  reviewCount: async () => (await req<{ count: number }>("GET", "/reviews?limit=0")).count,
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
  sources: () => req<LibrarySource[]>("GET", "/sources"),
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
  asset: (id: string, lang = "zh-TW") => req<AssetInfo>("GET", `/assets/${encodeURIComponent(id)}?lang=${encodeURIComponent(lang)}`),
  correct: (id: string, category: string) => req<unknown>("PATCH", `/assets/${encodeURIComponent(id)}/labels`, { category }),
  preset: (key: string, lang: string) =>
    req<{ key: string; categories: Category[] }>("GET", `/presets/${encodeURIComponent(key)}?lang=${encodeURIComponent(lang)}`),
};
