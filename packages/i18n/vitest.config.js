import { defineConfig } from "vitest/config";
import Tc from "../../tools/tc-reporters/vitest.js";
export default defineConfig({ test: { reporters: ["default", new Tc({ name: "i18n" })] } });
