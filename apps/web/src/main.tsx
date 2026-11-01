import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./lib/i18n";
import { applyTheme } from "./features/system/SettingsPage";
import "./styles.css";

try {
  applyTheme(localStorage.getItem("opa.theme") ?? "system");
} catch {
  /* storage unavailable: follow the system */
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={new QueryClient()}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
);
