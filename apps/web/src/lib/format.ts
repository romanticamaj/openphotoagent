// Locale formatting (04 §4.7): counts with grouping + plural rules, durations with Intl.DurationFormat when the
// runtime has it and an equivalent fallback when it does not (WebKitGTK, I18N-006).

const PHOTO: Record<string, Partial<Record<Intl.LDMLPluralRule, string>> & { other: string }> = {
  en: { one: "{n} photo", other: "{n} photos" },
  "zh-TW": { other: "{n} 張" },
};

export function photoCount(n: number, locale: string): string {
  const forms = PHOTO[locale] ?? PHOTO.en;
  const rule = new Intl.PluralRules(locale).select(n);
  const tpl = forms[rule] ?? forms.other;
  return tpl.replace("{n}", new Intl.NumberFormat(locale).format(n));
}

type Unit = "hours" | "minutes" | "seconds";
const UNITS: Record<string, Record<Unit, string>> = {
  en: { hours: "hr", minutes: "min", seconds: "sec" },
  "zh-TW": { hours: "小時", minutes: "分", seconds: "秒" },
};

function parts(ms: number): Array<[Unit, number]> {
  const s = Math.max(0, Math.round(ms / 1000));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const out: Array<[Unit, number]> = [];
  if (h) out.push(["hours", h]);
  if (m) out.push(["minutes", m]);
  if (sec || out.length === 0) out.push(["seconds", sec]);
  return out.slice(0, 2); // two most significant units: "1 hr, 5 min", "3 min, 20 sec"
}

export function formatDuration(ms: number, locale: string): string {
  const DF = (Intl as unknown as { DurationFormat?: new (l: string, o: object) => { format(d: object): string } })
    .DurationFormat;
  const p = parts(ms);
  if (DF) {
    return new DF(locale, { style: "short" }).format(Object.fromEntries(p));
  }
  const u = UNITS[locale] ?? UNITS.en;
  const nf = new Intl.NumberFormat(locale);
  if (locale.startsWith("zh")) return p.map(([k, v]) => `${nf.format(v)} ${u[k]}`).join(" ");
  return p.map(([k, v]) => `${nf.format(v)} ${u[k]}`).join(", ");
}

/** Decimal units (what download sizes and disks advertise); one decimal below 10, none above. */
export function formatBytes(n: number, locale: string): string {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = n;
  while (v >= 1000 && i < units.length - 1) {
    v /= 1000;
    i++;
  }
  const digits = i === 0 || v >= 10 ? 0 : 1;
  return `${new Intl.NumberFormat(locale, { maximumFractionDigits: digits, minimumFractionDigits: 0 }).format(v)} ${units[i]}`;
}
