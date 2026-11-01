import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import en from "@opa/i18n/locales/en.json";
import zhTW from "@opa/i18n/locales/zh-TW.json";

const lng = navigator.language?.toLowerCase().startsWith("zh") ? "zh-TW" : "en";
void i18n.use(initReactI18next).init({
  resources: { "zh-TW": { translation: zhTW }, en: { translation: en } },
  lng,
  fallbackLng: "en",
  interpolation: { escapeValue: false },
});
export default i18n;
