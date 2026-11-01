import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";
import Tc from "../../tools/tc-reporters/vitest.js";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
    setupFiles: ["src/test-setup.ts"],
    passWithNoTests: true,
    reporters: ["default", new Tc({ name: "web" })],
  },
});
