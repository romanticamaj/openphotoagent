import type { StorybookConfig } from "@storybook/react-vite";

// ADR-0003: Storybook 10 documents the components; Playwright screenshots the built iframe (VIS-001/003, ACT-006)
const config: StorybookConfig = {
  framework: "@storybook/react-vite",
  stories: ["../src/**/*.stories.tsx"],
  addons: ["@storybook/addon-a11y"],
  core: { disableTelemetry: true },
};
export default config;
