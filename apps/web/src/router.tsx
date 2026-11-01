import { Link, Outlet, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { OrganizeWizard } from "./features/organize/OrganizeWizard";
import { PhotosPage } from "./features/viewer/PhotosPage";

function Shell() {
  const { t } = useTranslation();
  const nav: Array<[string, string]> = [
    ["/", "nav.photos"],
    ["/organize", "nav.organize"],
  ];
  return (
    <div className="shell">
      <header className="topbar">
        <strong>{t("app.title")}</strong>
        <span className="text-xs text-[var(--text-muted)]">🔒 {t("nav.local")}</span>
      </header>
      <nav className="sidenav" aria-label={t("nav.label")}>
        {nav.map(([to, key]) => (
          <Link key={to} to={to} activeOptions={{ exact: to === "/" }} activeProps={{ "aria-current": "page" }}>
            {t(key)}
          </Link>
        ))}
      </nav>
      <main className="page" id="main">
        <Outlet />
      </main>
    </div>
  );
}

const root = createRootRoute({ component: Shell });
const photos = createRoute({ getParentRoute: () => root, path: "/", component: PhotosPage });
const organize = createRoute({
  getParentRoute: () => root,
  path: "/organize",
  component: () => (
    <div className="mx-auto max-w-3xl space-y-6 p-8">
      <OrganizeWizard />
    </div>
  ),
});

export const router = createRouter({ routeTree: root.addChildren([photos, organize]) });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
