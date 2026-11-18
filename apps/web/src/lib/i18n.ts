import i18n from "i18next";
import { initReactI18next } from "react-i18next";
type Entries = Record<string, Record<string, string>>;
/** Check ids contain dots ("decode.heic"); flatten them so they don't read as nested i18next keys. */
export const checkKey = (id: string) => id.replace(/\./g, "_");
function flat(e: Entries): Entries {
  return Object.fromEntries(Object.entries(e).map(([k, v]) => [checkKey(k), v]));
}
/** Single-user wording (08 §8.3) layered over the catalog; the team wording returns with the control plane. */
function withSolo(base: Entries, solo: Entries): Entries {
  const out: Entries = { ...base };
  for (const [k, v] of Object.entries(solo)) out[k] = { ...base[k], ...v };
  return out;
}

/** Pseudo-locale (I18N-004): accented and 40 % longer, placeholders untouched — catches truncation early. */
const ACC: Record<string, string> = { a: "à", e: "é", i: "ï", o: "ö", u: "ü", c: "ç", n: "ñ", A: "Å", E: "É", O: "Ö", U: "Ü" };
function pseudo(v: unknown): unknown {
  if (typeof v === "string") {
    const body = v.replace(/(\{[^}]+\})|([aeioucnAEOU])/g, (m, ph) => (ph ? ph : (ACC[m] ?? m)));
    return `［${body}${"~".repeat(Math.ceil(v.length * 0.4))}］`;
  }
  if (v && typeof v === "object") return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, pseudo(x)]));
  return v;
}

const forced = new URLSearchParams(globalThis.location?.search ?? "").get("lang");
const lng =
  forced === "pseudo" ? "pseudo" : (forced ?? navigator.language ?? "").toLowerCase().startsWith("zh") ? "zh-TW" : "en";
void i18n.use(initReactI18next).init({
  resources: {},
  partialBundledLanguages: true,
  lng,
  fallbackLng: false,
  // one placeholder syntax everywhere: `{name}`, as in the generated error catalog (06 §4.7)
  interpolation: { escapeValue: false, prefix: "{", suffix: "}" },
});

async function catalog(language: "zh-TW" | "en"): Promise<Record<string, unknown>> {
  if (language === "zh-TW") {
    const [base, errors, solo, checks] = await Promise.all([
      import("@opa/i18n/locales/zh-TW.json"),
      import("@opa/i18n/errors.zh-TW.json"),
      import("@opa/i18n/errors.solo.zh-TW.json"),
      import("@opa/i18n/checks.zh-TW.json"),
    ]);
    return { ...base.default, errors: withSolo(errors.default as Entries, solo.default as Entries), checks: flat(checks.default as Entries) };
  }
  const [base, errors, solo, checks] = await Promise.all([
    import("@opa/i18n/locales/en.json"),
    import("@opa/i18n/errors.en.json"),
    import("@opa/i18n/errors.solo.en.json"),
    import("@opa/i18n/checks.en.json"),
  ]);
  return { ...base.default, errors: withSolo(errors.default as Entries, solo.default as Entries), checks: flat(checks.default as Entries) };
}

/** One language's strings: the other one stays out of the first load (PERF-003). */
export async function loadLanguage(language: string): Promise<void> {
  if (i18n.hasResourceBundle(language, "translation")) return;
  const strings = language === "pseudo" ? (pseudo(await catalog("en")) as object) : await catalog(language === "zh-TW" ? "zh-TW" : "en");
  i18n.addResourceBundle(language, "translation", strings, true, true);
}

export async function switchLanguage(language: string): Promise<void> {
  await loadLanguage(language);
  await i18n.changeLanguage(language);
}

/** Resolves once the starting language is in: main.tsx waits for it before the first render. */
export const i18nReady = loadLanguage(lng);
export default i18n;
