import { Link, Outlet, createRootRoute, createRoute, createRouter, redirect } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { ActivityButton, ActivityDrawer, RecoveryCard } from "./features/activity/ActivityHost";
import { useTranslation } from "react-i18next";
import { OrganizeWizard } from "./features/organize/OrganizeWizard";
import { ModelCard, ModelsPage } from "./features/models/ModelsPage";
import { Welcome } from "./features/onboarding/Welcome";
import { DoctorPage } from "./features/system/DoctorPage";
import { SettingsPage } from "./features/system/SettingsPage";
import { PlanPage } from "./features/organize/PlanPage";
import { ReviewPage } from "./features/review/ReviewPage";
import { SearchBar } from "./features/search/SearchBar";
import { SearchPage } from "./features/search/SearchPage";
import { PhotosPage } from "./features/viewer/PhotosPage";
import { api } from "./lib/api";

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
        <Outlet />
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
