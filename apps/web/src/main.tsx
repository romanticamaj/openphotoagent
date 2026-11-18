import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "@fontsource-variable/inter";
import "@fontsource-variable/noto-sans-tc";
import { i18nReady } from "./lib/i18n";
import { applyTheme } from "./features/system/SettingsPage";
import "./styles.css";

try {
  applyTheme(localStorage.getItem("opa.theme") ?? "dark");
} catch {
  /* storage unavailable: follow the system */
}

await i18nReady;

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={new QueryClient()}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
);
