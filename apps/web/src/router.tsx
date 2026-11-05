import { Link, Outlet, createRootRoute, createRoute, createRouter, redirect } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { Suspense, lazy, useEffect, useRef, useState } from "react";
import { ActivityButton, ActivityDrawer, RecoveryCard } from "./features/activity/ActivityHost";
import { useTranslation } from "react-i18next";
import { SearchBar } from "./features/search/SearchBar";
import { PhotosPage } from "./features/viewer/PhotosPage";
import { api } from "./lib/api";
import { useServerEvent } from "./lib/events";
import { notificationFor, type OpStatusEvent } from "./lib/notify";
import { currentWorkspace, platform, setCsrf, setWorkspace } from "./lib/platform";

// only the photos route ships in the first load (PERF-003); every other page is its own chunk, fetched once the
// first screen has settled so that moving between pages stays instant
const PAGES = [
  () => import("./features/organize/OrganizeWizard"),
  () => import("./features/onboarding/Welcome"),
  () => import("./features/system/DoctorPage"),
  () => import("./features/system/SettingsPage"),
  () => import("./features/organize/PlanPage"),
  () => import("./features/review/ReviewPage"),
  () => import("./features/search/SearchPage"),
  () => import("./features/models/ModelsPage"),
  () => import("./features/duplicates/DuplicatesPage"),
  () => import("./features/mcp/IntegrationsPage"),
  () => import("./features/mcp/ApprovalPage"),
  () => import("./features/people/PeoplePage"),
  () => import("./features/ask/AskPanel"),
  () => import("./features/ask/AssistantSettingsPage"),
  () => import("./features/map/MapPage"),
];
export function prefetchPages(delayMs = 3000) {
  if (typeof window === "undefined") return;
  window.setTimeout(() => PAGES.forEach((load) => void load().catch(() => undefined)), delayMs);
}
const OrganizeWizard = lazy(() => import("./features/organize/OrganizeWizard").then((m) => ({ default: m.OrganizeWizard })));
const Welcome = lazy(() => import("./features/onboarding/Welcome").then((m) => ({ default: m.Welcome })));
const DoctorPage = lazy(() => import("./features/system/DoctorPage").then((m) => ({ default: m.DoctorPage })));
const SettingsPage = lazy(() => import("./features/system/SettingsPage").then((m) => ({ default: m.SettingsPage })));
const PlanPage = lazy(() => import("./features/organize/PlanPage").then((m) => ({ default: m.PlanPage })));
const ReviewPage = lazy(() => import("./features/review/ReviewPage").then((m) => ({ default: m.ReviewPage })));
const SearchPage = lazy(() => import("./features/search/SearchPage").then((m) => ({ default: m.SearchPage })));
const DuplicatesPage = lazy(() => import("./features/duplicates/DuplicatesPage").then((m) => ({ default: m.DuplicatesPage })));
const IntegrationsPage = lazy(() => import("./features/mcp/IntegrationsPage").then((m) => ({ default: m.IntegrationsPage })));
const ApprovalPage = lazy(() => import("./features/mcp/ApprovalPage").then((m) => ({ default: m.ApprovalPage })));
const ApprovalInbox = lazy(() => import("./features/mcp/ApprovalPage").then((m) => ({ default: m.ApprovalInbox })));
const PeoplePage = lazy(() => import("./features/people/PeoplePage").then((m) => ({ default: m.PeoplePage })));
const FaceReview = lazy(() => import("./features/people/PeoplePage").then((m) => ({ default: m.FaceReview })));
const FaceSettingsPage = lazy(() => import("./features/people/PeoplePage").then((m) => ({ default: m.FaceSettingsPage })));
const AskPanel = lazy(() => import("./features/ask/AskPanel").then((m) => ({ default: m.AskPanel })));
const AssistantSettingsPage = lazy(() => import("./features/ask/AssistantSettingsPage").then((m) => ({ default: m.AssistantSettingsPage })));
const MapPage = lazy(() => import("./features/map/MapPage").then((m) => ({ default: m.MapPage })));
const ModelsPage = lazy(() => import("./features/models/ModelsPage").then((m) => ({ default: m.ModelsPage })));
const LoginPage = lazy(() => import("./features/auth/LoginPage").then((m) => ({ default: m.LoginPage })));
const ModelCard = lazy(() => import("./features/models/ModelsPage").then((m) => ({ default: m.ModelCard })));

/** Control plane only (v0.5): which workspace this window works in; others look like they do not exist. */
function WorkspaceSwitcher() {
  const { t } = useTranslation();
  const list = useQuery({ queryKey: ["workspaces"], queryFn: api.workspaces, staleTime: 60_000 });
  if (!list.data || list.data.length < 2) return null;
  return (
    <label className="text-xs">
      <span className="sr-only">{t("auth.workspace")}</span>
      <select
        value={currentWorkspace() ?? "default"}
        onChange={(e) => {
          setWorkspace(e.target.value);
          window.location.assign("/"); // every page, query and stream belongs to the chosen workspace
        }}
        className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--surface-1)] px-1"
      >
        {list.data.map((w) => (
          <option key={w.id} value={w.id}>
            {w.name}
          </option>
        ))}
      </select>
    </label>
  );
}

function Root() {
  // the sign-in page stands alone: nothing in the shell may call the API before there is a session
  if (typeof window !== "undefined" && window.location.pathname === "/login") {
    return (
      <Suspense fallback={null}>
        <LoginPage />
      </Suspense>
    );
  }
  return <Shell />;
}

function Shell() {
  const { t } = useTranslation();
  const [activity, setActivity] = useState(false);
  const [asking, setAskingRaw] = useState(false);
  const askOpener = useRef<HTMLElement | null>(null);
  const askButton = useRef<HTMLButtonElement>(null);
  // 問 AI and the Activity Center share the right-hand slot: opening one closes the other (ADR-0013)
  const setAsking = (v: boolean) => {
    if (v) {
      askOpener.current = document.activeElement as HTMLElement | null;
      setActivity(false);
    } else {
      // focus goes back to whatever opened the panel, or to the 問 AI button when that is gone (a closed drawer)
      window.setTimeout(() => (askOpener.current?.isConnected && askOpener.current !== document.body ? askOpener.current : askButton.current)?.focus(), 0);
    }
    setAskingRaw(v);
  };
  const setActivityOpen = (v: boolean) => {
    if (v) setAskingRaw(false);
    setActivity(v);
  };
  // ⌘I / Ctrl+I toggles 問 AI; ⌘J stays with the Activity Center (06 §6.1, ACT-005; ADR-0013)
  const askingRef = useRef(asking);
  askingRef.current = asking;
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && !e.altKey && e.key.toLowerCase() === "i") {
        e.preventDefault();
        setAsking(!askingRef.current);
      }
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, []);
  // single-user desktop/all-in-one: no organisation, workspace or worker management in the UI (08 §8, DESK-032)
  const caps = useQuery({ queryKey: ["capabilities"], queryFn: api.capabilities, staleTime: Infinity });
  const multi = caps.data ? !caps.data.single_user : false;
  // control plane: after a reload the CSRF token comes back from the session (02 §6.3)
  const me = useQuery({ queryKey: ["me"], queryFn: api.me, enabled: multi, staleTime: Infinity });
  useEffect(() => {
    if (me.data) setCsrf(me.data.csrf);
  }, [me.data]);
  useEffect(() => prefetchPages(), []);
  useServerEvent("operation.status", (d) => {
    const n = platform().notify && notificationFor(d as OpStatusEvent);
    if (n) platform().notify!(t(n.key), n.route);
  });
  // 「人物」 appears only once faces are on (07 §4.1; FACE-GATE-001)
  const faces = useQuery({ queryKey: ["face-settings"], queryFn: api.faceSettings, staleTime: 30_000 });
  const nav: Array<[string, string]> = [
    ["/", "nav.photos"],
    ...(faces.data?.enabled ? ([["/people", "nav.people"]] as Array<[string, string]>) : []),
    ["/map", "nav.map"],
    ["/organize", "nav.organize"],
    ["/review", "nav.review"],
    ["/duplicates", "nav.duplicates"],
    ["/models", "nav.models"],
    ["/system/doctor", "nav.doctor"],
    ["/settings", "nav.settings"],
  ];
  return (
    <div className="shell" data-single-user={caps.data ? String(caps.data.single_user) : undefined}>
      <header className="topbar">
        <strong>{t("app.title")}</strong>
        <SearchBar />
        {multi ? (
          <>
            <WorkspaceSwitcher />
            <button
              type="button"
              onClick={() => void api.logout().finally(() => window.location.assign("/login"))}
              className="rounded-[var(--radius-sm)] px-2 py-1 text-xs hover:bg-[var(--surface-2)]"
            >
              {t("auth.signOut")}
            </button>
          </>
        ) : (
          <span className="text-xs text-[var(--text-muted)]">🔒 {t("nav.local")}</span>
        )}
        <button ref={askButton} type="button" aria-expanded={asking} onClick={() => setAsking(!asking)} className="rounded-[var(--radius-sm)] px-2 py-1 hover:bg-[var(--surface-2)]">
          {t("ask.open")}
        </button>
        <ActivityButton onToggle={() => setActivityOpen(!activity)} />
      </header>
      <nav className="sidenav" aria-label={t("nav.label")}>
        {nav.map(([to, key]) => (
          <Link key={to} to={to} activeOptions={{ exact: to === "/" }} activeProps={{ "aria-current": "page" }}>
            {t(key)}
          </Link>
        ))}
      </nav>
      <main className="page" id="main">
        <RecoveryCard onReview={() => setActivityOpen(true)} />
        <Suspense fallback={null}>
          <ApprovalInbox />
        </Suspense>
        <Suspense fallback={<div aria-busy="true" />}>
          <Outlet />
        </Suspense>
      </main>
      {asking ? (
        <Suspense fallback={null}>
          <AskPanel open={asking} onClose={() => setAsking(false)} />
        </Suspense>
      ) : null}
      <ActivityDrawer open={activity} setOpen={setActivityOpen} />
    </div>
  );
}

const root = createRootRoute({ component: Root });
const photos = createRoute({
  getParentRoute: () => root,
  path: "/",
  component: PhotosPage,
  // an empty library (no folder added yet) starts with onboarding (04 §8.2)
  beforeLoad: async () => {
    const sources = await api.sources().catch(() => null);
    if (sources !== null && sources.length === 0) throw redirect({ to: "/welcome" });
  },
});
const plan = createRoute({
  getParentRoute: () => root,
  path: "/plans/$planId",
  component: function PlanRoute() {
    const { planId } = plan.useParams();
    return <PlanPage planId={planId} />;
  },
});
const reviewRoute = createRoute({
  getParentRoute: () => root,
  path: "/review",
  validateSearch: (s: Record<string, unknown>) => ({ job: typeof s.job === "string" ? s.job : "" }),
  component: function ReviewRoute() {
    const { job } = reviewRoute.useSearch();
    return <ReviewPage jobId={job} />;
  },
});
const duplicatesRoute = createRoute({ getParentRoute: () => root, path: "/duplicates", component: DuplicatesPage });
const modelsRoute = createRoute({ getParentRoute: () => root, path: "/models", component: ModelsPage });
const modelRoute = createRoute({
  getParentRoute: () => root,
  path: "/models/$modelId",
  component: function ModelRoute() {
    const { modelId } = modelRoute.useParams();
    return <ModelCard modelId={modelId} />;
  },
});
const doctorRoute = createRoute({ getParentRoute: () => root, path: "/system/doctor", component: DoctorPage });
const settingsRoute = createRoute({ getParentRoute: () => root, path: "/settings", component: SettingsPage });
const integrationsRoute = createRoute({ getParentRoute: () => root, path: "/settings/integrations", component: IntegrationsPage });
const peopleRoute = createRoute({ getParentRoute: () => root, path: "/people", component: PeoplePage });
const faceReviewRoute = createRoute({ getParentRoute: () => root, path: "/people/review", component: FaceReview });
const mapRoute = createRoute({ getParentRoute: () => root, path: "/map", component: MapPage });
const assistantRoute = createRoute({ getParentRoute: () => root, path: "/settings/assistant", component: AssistantSettingsPage });
const faceSettingsRoute = createRoute({ getParentRoute: () => root, path: "/settings/faces", component: FaceSettingsPage });
const approvalRoute = createRoute({ getParentRoute: () => root, path: "/approvals/$approvalId", component: ApprovalPage });
const welcome = createRoute({ getParentRoute: () => root, path: "/welcome", component: Welcome });
const search = createRoute({
  getParentRoute: () => root,
  path: "/search",
  validateSearch: (s: Record<string, unknown>) => ({ q: typeof s.q === "string" ? s.q : "" }),
  component: function SearchRoute() {
    const { q } = search.useSearch();
    return <SearchPage q={q} />;
  },
});
const organize = createRoute({
  getParentRoute: () => root,
  path: "/organize",
  component: () => (
    <div className="mx-auto max-w-3xl space-y-6 p-8">
      <OrganizeWizard />
    </div>
  ),
});

export const router = createRouter({ routeTree: root.addChildren([photos, welcome, organize, plan, reviewRoute, search, duplicatesRoute, modelsRoute, modelRoute, doctorRoute, settingsRoute, integrationsRoute, approvalRoute, peopleRoute, faceReviewRoute, faceSettingsRoute, assistantRoute, mapRoute]) });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
