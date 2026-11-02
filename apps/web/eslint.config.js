import i18next from "eslint-plugin-i18next";
import opa from "eslint-plugin-opa";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "e2e/.run", "storybook-static"] },
  ...tseslint.configs.recommended,
  {
    files: ["src/**/*.{ts,tsx}", "e2e/**/*.ts"],
    languageOptions: { parserOptions: { ecmaFeatures: { jsx: true } } },
    plugins: { opa },
    rules: { "opa/no-bare-disabled": "error", "@typescript-eslint/no-non-null-assertion": "off" },
  },
  {
    // I18N-002: every user-facing JSX text goes through i18n (symbols and language names are allow-listed)
    files: ["src/**/*.tsx"],
    ignores: ["src/**/*.test.tsx"],
    plugins: { i18next },
    rules: {
      "i18next/no-literal-string": [
        "error",
        { mode: "jsx-text-only", "jsx-components": { exclude: ["kbd"] }, words: { exclude: ["[×‹›−+—~·•●◐○?✓⚠⏸⏏◌↺🔒=:：/|()（）]+", "繁體中文", "English", "[0-9]+"] } },
      ],
    },
  },
);
