import { useAskRequest } from "./lib/askBus";
import { useNavDrawer } from "./lib/navDrawer";
import { usePageArrival } from "./lib/pageTitle";
import { Link, Outlet, createRootRoute, createRoute, createRouter, redirect, useRouterState } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { Suspense, lazy, useEffect, useRef, useState } from "react";
import { ActivityButton, ActivityDrawer, RecoveryCard } from "./features/activity/ActivityHost";
import { useTranslation } from "react-i18next";
import { SearchBar } from "./features/search/SearchBar";
import { PhotosPage } from "./features/viewer/PhotosPage";
import { ProgressDock } from "./features/activity/ProgressDock";
import { api } from "./lib/api";
import { useServerEvent } from "./lib/events";
import { navSections } from "./lib/nav";
import { notificationFor, type OpStatusEvent } from "./lib/notify";
import { currentWorkspace, platform, setCsrf, setWorkspace } from "./lib/platform";

// only the photos route ships in the first load (PERF-003); every other page is its own chunk, fetched once the
// first screen has settled so that moving between pages stays instant
const PAGES = [
  () => import("./features/organize/OrganizeWizard"),
  () => import("./features/library/ImportPage"),
  () => import("./features/library/ExplorePage"),
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
  () => import("./features/albums/AlbumsPage"),
];
export function prefetchPages(delayMs = 3000) {
  if (typeof window === "undefined") return;
  window.setTimeout(() => PAGES.forEach((load) => void load().catch(() => undefined)), delayMs);
}
const OrganizeWizard = lazy(() => import("./features/organize/OrganizeWizard").then((m) => ({ default: m.OrganizeWizard })));
const ImportPage = lazy(() => import("./features/library/ImportPage").then((m) => ({ default: m.ImportPage })));
const ExplorePage = lazy(() => import("./features/library/ExplorePage").then((m) => ({ default: m.ExplorePage })));
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
const AlbumsPage = lazy(() => import("./features/albums/AlbumsPage").then((m) => ({ default: m.AlbumsPage })));
const AlbumPage = lazy(() => import("./features/albums/AlbumsPage").then((m) => ({ default: m.AlbumPage })));
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
  useAskRequest(() => setAsking(true), false); // a page hands over a question; the panel takes it (ASK-UI-020/021)
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
  const faces = useQuery({ queryKey: ["face-settings"], queryFn: api.faceSettings, staleTime: 30_000 });
  const pending = useQuery({ queryKey: ["reviews", "count"], queryFn: () => api.reviewCount(), refetchInterval: 10_000 });
  const path = useRouterState({ select: (st) => st.location.pathname });
  const menuButton = useRef<HTMLButtonElement>(null);
  const sidebar = useRef<HTMLDivElement>(null);
  const drawer = useNavDrawer(path, { toggle: menuButton, panel: sidebar }); // a drawer in a narrow window (VIEW-049/050)
  const href = useRouterState({ select: (st) => st.location.pathname + st.location.searchStr });
  usePageArrival(href, t); // the window title and focus follow the page, its query too (VIEW-054/059)
  return (
    <div className="shell" data-single-user={caps.data ? String(caps.data.single_user) : undefined} data-nav-open={drawer.open ? "true" : undefined}>
      {/* the first Tab: past the search box and the sidebar, to the page's photos (A11Y-013) */}
      <a
        href="#main"
        className="skip-link"
        onClick={(e) => {
          e.preventDefault();
          const main = document.getElementById("main");
          const target = main?.querySelector<HTMLElement>('[role="gridcell"][tabindex="0"]') ?? main;
          target?.focus();
        }}
      >
        {t("app.skipToMain")}
      </a>
      <header className="topbar">
        <button ref={menuButton} type="button" className="nav-toggle" aria-expanded={drawer.open} aria-controls="app-sidebar" onClick={drawer.toggle}>
          {t("nav.menu")}
        </button>
        <strong className="app-name">{t("app.title")}</strong>
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
          <span className="local-badge text-xs text-[var(--text-muted)]">🔒 {t("nav.local")}</span>
        )}
        <button ref={askButton} type="button" aria-expanded={asking} onClick={() => setAsking(!asking)} className="rounded-[var(--radius-sm)] px-2 py-1 hover:bg-[var(--surface-2)]">
          {t("ask.open")}
        </button>
        <ActivityButton onToggle={() => setActivityOpen(!activity)} />
      </header>
      {/* the nav and the progress card share the sidebar column: the card never covers 設定 (ACT-010) */}
      {drawer.open ? <div className="nav-scrim" aria-hidden="true" onClick={drawer.close} /> : null}
      <div
        className="sidebar"
        id="app-sidebar"
        ref={sidebar}
        {...(drawer.open ? { role: "dialog", "aria-modal": true, "aria-label": t("nav.label") } : {})}
      >
      <nav className="sidenav" aria-label={t("nav.label")}>
        {navSections(Boolean(faces.data?.enabled)).map((g) => (
          <div key={g.key} className="sidenav-group">
            <p aria-hidden="true">{t(g.key)}</p>
            {g.items.map(({ to, label, hint }) => (
              <Link key={to} to={to} activeOptions={{ exact: to === "/" }} activeProps={{ "aria-current": "page" }} aria-description={hint ? t(hint) : undefined}>
                {hint ? (
                  // the name stays the link's name; the line under it says what the page does (IMP-040)
                  <span className="nav-two-line">
                    <span>{t(label)}</span>
                    <span aria-hidden="true" className="nav-hint">{t(hint)}</span>
                  </span>
                ) : (
                  t(label)
                )}
                {to === "/review" && pending.data ? <span className="nav-badge tnum">{pending.data.toLocaleString()}</span> : null}
              </Link>
            ))}
          </div>
        ))}
      </nav>
      <ProgressDock />
      </div>
      {/* behind the open drawer the page is out of reach for a screen reader too (VIEW-051, audit round 86) */}
      <main className="page" id="main" tabIndex={-1} inert={drawer.open || undefined}>
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
const peopleRoute = createRoute({
  getParentRoute: () => root,
  path: "/people",
  // ?person= opens that person (from 「照片中的人物」 in the Lightbox, VIEW-021)
  validateSearch: (s: Record<string, unknown>) => ({ person: typeof s.person === "string" ? s.person : undefined }),
  component: function People() {
    const { person } = peopleRoute.useSearch();
    return <PeoplePage initial={person} />;
  },
});
const faceReviewRoute = createRoute({ getParentRoute: () => root, path: "/people/review", component: FaceReview });
// a person's own page (FACE-UI-023): every photo of them, with 「← 人物」 back to the wall
const personRoute = createRoute({
  getParentRoute: () => root,
  path: "/people/$personId",
  component: function Person() {
    const { personId } = personRoute.useParams();
    return <PeoplePage initial={personId} />;
  },
});
const mapRoute = createRoute({ getParentRoute: () => root, path: "/map", component: MapPage });
const albumsRoute = createRoute({ getParentRoute: () => root, path: "/albums", component: AlbumsPage });
const albumRoute = createRoute({
  getParentRoute: () => root,
  path: "/albums/$albumId",
  component: function AlbumRoute() {
    const { albumId } = albumRoute.useParams();
    return <AlbumPage albumId={albumId} />;
  },
});
const assistantRoute = createRoute({ getParentRoute: () => root, path: "/settings/assistant", component: AssistantSettingsPage });
const faceSettingsRoute = createRoute({ getParentRoute: () => root, path: "/settings/faces", component: FaceSettingsPage });
const approvalRoute = createRoute({ getParentRoute: () => root, path: "/approvals/$approvalId", component: ApprovalPage });
const welcome = createRoute({ getParentRoute: () => root, path: "/welcome", component: Welcome });
const search = createRoute({
  getParentRoute: () => root,
  path: "/search",
  validateSearch: (s: Record<string, unknown>): { q: string; tag?: string; year?: string; name?: string } => ({
    q: typeof s.q === "string" ? s.q : "",
    ...(typeof s.tag === "string" ? { tag: s.tag } : {}),
    ...(typeof s.name === "string" ? { name: s.name } : {}),
    ...(typeof s.year === "string" || typeof s.year === "number" ? { year: String(s.year) } : {}),
  }),
  component: function SearchRoute() {
    const { q, tag, year, name } = search.useSearch();
    return <SearchPage q={q} tag={tag} year={year} name={name} />;
  },
});
const importRoute = createRoute({ getParentRoute: () => root, path: "/import", component: ImportPage });
const exploreRoute = createRoute({
  getParentRoute: () => root,
  path: "/explore",
  validateSearch: (s: Record<string, unknown>) => ({
    category: typeof s.category === "string" ? s.category : undefined,
    tag: typeof s.tag === "string" ? s.tag : undefined,
  }),
  component: function Explore() {
    const { category, tag } = exploreRoute.useSearch();
    const nav = exploreRoute.useNavigate();
    const initial = category ? { kind: "category" as const, value: category } : tag ? { kind: "tag" as const, value: tag } : null;
    return (
      <ExplorePage
        initial={initial}
        onPick={(p) => void nav({ search: { category: p?.kind === "category" ? p.value : undefined, tag: p?.kind === "tag" ? p.value : undefined }, replace: true })}
      />
    );
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

export const router = createRouter({ routeTree: root.addChildren([photos, welcome, importRoute, exploreRoute, organize, plan, reviewRoute, search, duplicatesRoute, modelsRoute, modelRoute, doctorRoute, settingsRoute, integrationsRoute, approvalRoute, peopleRoute, faceReviewRoute, personRoute, faceSettingsRoute, assistantRoute, mapRoute, albumsRoute, albumRoute]) });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
