import "@fontsource-variable/inter";
import "@fontsource-variable/noto-sans-tc";
import type { Preview } from "@storybook/react-vite";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n, { loadLanguage } from "../src/lib/i18n";
import "../src/styles.css";

const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });

const preview: Preview = {
  loaders: [async () => void (await Promise.all([loadLanguage("zh-TW"), loadLanguage("en")]))],
  globalTypes: {
    theme: { description: "Theme", toolbar: { items: ["light", "dark"] } },
    lang: { description: "Language", toolbar: { items: ["zh-TW", "en"] } },
  },
  initialGlobals: { theme: "light", lang: "zh-TW" },
  decorators: [
    (Story, ctx) => {
      document.documentElement.setAttribute("data-theme", String(ctx.globals.theme ?? "light"));
      if (i18n.language !== ctx.globals.lang) void i18n.changeLanguage(String(ctx.globals.lang ?? "zh-TW"));
      return (
        <QueryClientProvider client={qc}>
          <div style={{ padding: 16, background: "var(--bg)", color: "var(--text)" }}>
            <Story />
          </div>
        </QueryClientProvider>
      );
    },
  ],
  parameters: { a11y: { test: "error" } },
};
export default preview;
