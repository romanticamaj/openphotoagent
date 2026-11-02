import { Link, Outlet, createRootRoute, createRoute, createRouter, redirect } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { Suspense, lazy, useEffect, useState } from "react";
import { ActivityButton, ActivityDrawer, RecoveryCard } from "./features/activity/ActivityHost";
import { useTranslation } from "react-i18next";
import { SearchBar } from "./features/search/SearchBar";
import { PhotosPage } from "./features/viewer/PhotosPage";
import { api } from "./lib/api";
import { useServerEvent } from "./lib/events";
import { notificationFor, type OpStatusEvent } from "./lib/notify";
import { platform } from "./lib/platform";

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
const FaceOptInCard = lazy(() => import("./features/people/PeoplePage").then((m) => ({ default: m.FaceOptInCard })));
const ModelsPage = lazy(() => import("./features/models/ModelsPage").then((m) => ({ default: m.ModelsPage })));
const ModelCard = lazy(() => import("./features/models/ModelsPage").then((m) => ({ default: m.ModelCard })));

function Shell() {
  const { t } = useTranslation();
  const [activity, setActivity] = useState(false);
  // single-user desktop/all-in-one: no organisation, workspace or worker management in the UI (08 §8, DESK-032)
  const caps = useQuery({ queryKey: ["capabilities"], queryFn: api.capabilities, staleTime: Infinity });
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
        <span className="text-xs text-[var(--text-muted)]">🔒 {t("nav.local")}</span>
        <ActivityButton onToggle={() => setActivity(!activity)} />
      </header>
      <nav className="sidenav" aria-label={t("nav.label")}>
        {nav.map(([to, key]) => (
          <Link key={to} to={to} activeOptions={{ exact: to === "/" }} activeProps={{ "aria-current": "page" }}>
            {t(key)}
          </Link>
        ))}
      </nav>
      <main className="page" id="main">
        <RecoveryCard onReview={() => setActivity(true)} />
        <Suspense fallback={null}>
          <ApprovalInbox />
          <FaceOptInCard />
        </Suspense>
        <Suspense fallback={<div aria-busy="true" />}>
          <Outlet />
        </Suspense>
      </main>
      <ActivityDrawer open={activity} setOpen={setActivity} />
    </div>
  );
}

const root = createRootRoute({ component: Shell });
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

export const router = createRouter({ routeTree: root.addChildren([photos, welcome, organize, plan, reviewRoute, search, duplicatesRoute, modelsRoute, modelRoute, doctorRoute, settingsRoute, integrationsRoute, approvalRoute, peopleRoute, faceReviewRoute, faceSettingsRoute]) });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
