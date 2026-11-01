import opa from "eslint-plugin-opa";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "e2e/.run"] },
  ...tseslint.configs.recommended,
  {
    files: ["src/**/*.{ts,tsx}", "e2e/**/*.ts"],
    languageOptions: { parserOptions: { ecmaFeatures: { jsx: true } } },
    plugins: { opa },
    rules: { "opa/no-bare-disabled": "error", "@typescript-eslint/no-non-null-assertion": "off" },
  },
);
