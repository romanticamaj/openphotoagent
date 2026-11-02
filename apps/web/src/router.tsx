import { Link, Outlet, createRootRoute, createRoute, createRouter, redirect } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { Suspense, lazy, useState } from "react";
import { ActivityButton, ActivityDrawer, RecoveryCard } from "./features/activity/ActivityHost";
import { useTranslation } from "react-i18next";
import { SearchBar } from "./features/search/SearchBar";
import { PhotosPage } from "./features/viewer/PhotosPage";
import { api } from "./lib/api";

// only the photos route ships in the first load (PERF-003); every other page is its own chunk
const OrganizeWizard = lazy(() => import("./features/organize/OrganizeWizard").then((m) => ({ default: m.OrganizeWizard })));
const Welcome = lazy(() => import("./features/onboarding/Welcome").then((m) => ({ default: m.Welcome })));
const DoctorPage = lazy(() => import("./features/system/DoctorPage").then((m) => ({ default: m.DoctorPage })));
const SettingsPage = lazy(() => import("./features/system/SettingsPage").then((m) => ({ default: m.SettingsPage })));
const PlanPage = lazy(() => import("./features/organize/PlanPage").then((m) => ({ default: m.PlanPage })));
const ReviewPage = lazy(() => import("./features/review/ReviewPage").then((m) => ({ default: m.ReviewPage })));
const SearchPage = lazy(() => import("./features/search/SearchPage").then((m) => ({ default: m.SearchPage })));
const ModelsPage = lazy(() => import("./features/models/ModelsPage").then((m) => ({ default: m.ModelsPage })));
const ModelCard = lazy(() => import("./features/models/ModelsPage").then((m) => ({ default: m.ModelCard })));

function Shell() {
  const { t } = useTranslation();
  const [activity, setActivity] = useState(false);
  // single-user desktop/all-in-one: no organisation, workspace or worker management in the UI (08 §8, DESK-032)
  const caps = useQuery({ queryKey: ["capabilities"], queryFn: api.capabilities, staleTime: Infinity });
  const nav: Array<[string, string]> = [
    ["/", "nav.photos"],
    ["/organize", "nav.organize"],
    ["/review", "nav.review"],
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

export const router = createRouter({ routeTree: root.addChildren([photos, welcome, organize, plan, reviewRoute, search, modelsRoute, modelRoute, doctorRoute, settingsRoute]) });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
