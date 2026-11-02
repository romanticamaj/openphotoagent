import { Link } from "@tanstack/react-router";
// Settings (04 §3.12, single-user): language, appearance, and the local engine's photo folders (read-only here —
// adding a folder is done in the app's own folder picker, never through the API: invariant 2).
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../../lib/api";

export function applyTheme(theme: string) {
  if (theme === "system") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", theme);
}

function stored(): string {
  try {
    return localStorage.getItem("opa.theme") ?? "system";
  } catch {
    return "system";
  }
}

export function SettingsPage() {
  const { t, i18n } = useTranslation();
  const [theme, setTheme] = useState(stored);
  const roots = useQuery({ queryKey: ["roots"], queryFn: api.roots });
  return (
    <section className="mx-auto max-w-2xl space-y-6 p-8" aria-labelledby="settings-h">
      <h1 id="settings-h" className="text-xl font-semibold">{t("settings.title")}</h1>
      <label className="flex flex-col gap-1">
        {t("settings.language")}
        <select value={i18n.language} onChange={(e) => void i18n.changeLanguage(e.target.value)} className="w-60 rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--bg)] px-2 py-1">
          <option value="zh-TW">繁體中文</option>
          <option value="en">English</option>
        </select>
      </label>
      <label className="flex flex-col gap-1">
        {t("settings.theme")}
        <select
          value={theme}
          onChange={(e) => {
            setTheme(e.target.value);
            applyTheme(e.target.value);
            try {
              localStorage.setItem("opa.theme", e.target.value);
            } catch {
              /* private mode: the choice lasts for this session */
            }
          }}
          className="w-60 rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--bg)] px-2 py-1"
        >
          <option value="system">{t("settings.themeSystem")}</option>
          <option value="light">{t("settings.themeLight")}</option>
          <option value="dark">{t("settings.themeDark")}</option>
        </select>
      </label>
      <section aria-labelledby="mcp-h" className="space-y-2">
        <h2 id="mcp-h" className="font-semibold">{t("mcp.title")}</h2>
        <p className="text-sm text-[var(--text-muted)]">{t("mcp.intro")}</p>
        <Link to="/settings/integrations" className="underline">{t("mcp.open")}</Link>
      </section>
      <section aria-labelledby="engine-h" className="space-y-2">
        <h2 id="engine-h" className="font-semibold">{t("settings.engine")}</h2>
        <h3 className="text-sm text-[var(--text-muted)]">{t("settings.folders")}</h3>
        <ul className="text-sm">
          {(roots.data ?? []).map((r) => (
            <li key={r.alias}>
              {r.alias} · {r.mode === "rw" ? t("settings.writable") : t("settings.readOnly")}
            </li>
          ))}
        </ul>
      </section>
    </section>
  );
}
