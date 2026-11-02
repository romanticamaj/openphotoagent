import tailwind from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwind()],
  // fonts are never inlined: ~100 Noto Sans TC subsets as base64 would bloat the first-load CSS (PERF-003)
  build: { assetsInlineLimit: (file) => (/\.(woff2?|ttf|otf)$/.test(file) ? false : undefined) },
  server: { proxy: { "/api": "http://127.0.0.1:47800" } },
});
