import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import en from "@opa/i18n/locales/en.json";
import zhTW from "@opa/i18n/locales/zh-TW.json";
import errorsEn from "@opa/i18n/errors.en.json";
import errorsZh from "@opa/i18n/errors.zh-TW.json";
import checksEn from "@opa/i18n/checks.en.json";
import checksZh from "@opa/i18n/checks.zh-TW.json";

const forced = new URLSearchParams(globalThis.location?.search ?? "").get("lang");
const lng = (forced ?? navigator.language ?? "").toLowerCase().startsWith("zh") ? "zh-TW" : "en";
void i18n.use(initReactI18next).init({
  resources: {
    "zh-TW": { translation: { ...zhTW, errors: errorsZh, checks: checksZh } },
    en: { translation: { ...en, errors: errorsEn, checks: checksEn } },
  },
  lng,
  fallbackLng: "en",
  // one placeholder syntax everywhere: `{name}`, as in the generated error catalog (06 §4.7)
  interpolation: { escapeValue: false, prefix: "{", suffix: "}" },
});
export default i18n;
