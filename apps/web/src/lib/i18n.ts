import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import en from "@opa/i18n/locales/en.json";
import zhTW from "@opa/i18n/locales/zh-TW.json";
import errorsEn from "@opa/i18n/errors.en.json";
import errorsZh from "@opa/i18n/errors.zh-TW.json";
import soloEn from "@opa/i18n/errors.solo.en.json";
import soloZh from "@opa/i18n/errors.solo.zh-TW.json";
import checksEn from "@opa/i18n/checks.en.json";
import checksZh from "@opa/i18n/checks.zh-TW.json";

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
  resources: {
    "zh-TW": { translation: { ...zhTW, errors: withSolo(errorsZh as Entries, soloZh as Entries), checks: flat(checksZh as Entries) } },
    en: { translation: { ...en, errors: withSolo(errorsEn as Entries, soloEn as Entries), checks: flat(checksEn as Entries) } },
    pseudo: {
      translation: pseudo({ ...en, errors: withSolo(errorsEn as Entries, soloEn as Entries), checks: flat(checksEn as Entries) }) as Record<string, unknown>,
    },
  },
  lng,
  fallbackLng: "en",
  // one placeholder syntax everywhere: `{name}`, as in the generated error catalog (06 §4.7)
  interpolation: { escapeValue: false, prefix: "{", suffix: "}" },
});
export default i18n;
