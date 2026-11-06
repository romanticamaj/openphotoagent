import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";
import { i18nReady, loadLanguage } from "./lib/i18n";

// Vitest runs without globals, so Testing Library cannot register its own cleanup.
afterEach(() => cleanup());

// the app loads one language; tests switch between them freely
await Promise.all([i18nReady, loadLanguage("zh-TW"), loadLanguage("en")]);
