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

function roundedMinutes(s: number): number {
  const m = s / 60;
  if (m < 10) return Math.max(1, Math.round(m));
  if (m < 60) return Math.round(m / 5) * 5;
  if (m < 360) return Math.round(m / 10) * 10;
  return Math.round(m / 30) * 30;
}

function roughDuration(mins: number, zh: boolean): string {
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  if (zh) return `約 ${h ? `${h} 小時` : ""}${h && m ? " " : ""}${m || !h ? `${m} 分鐘` : ""}`;
  return `About ${h ? `${h} h` : ""}${h && m ? " " : ""}${m || !h ? `${m} min` : ""}`;
}

/** What the user reads for an ETA (06 §5.5.6). null: this state has no number to show. */
export function etaText(eta: { state?: string; p50_s?: number; since_s?: number; samples?: number }, locale: string): string | null {
  const zh = locale.startsWith("zh");
  const s = eta.p50_s;
  if (eta.state === "stalled") {
    const since = formatDuration((eta.since_s ?? 0) * 1000, locale);
    return zh ? `沒有進展 ${since}` : `No progress for ${since}`;
  }
  if (s == null) return null;
  if (eta.state === "precise") {
    const clock = `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;
    return zh ? `剩 ${clock}` : `${clock} left`;
  }
  if (eta.state !== "rough") return null;
  if (s < 60) {
    // with enough samples, seconds in steps of 10 (ETA-032): 「不到 1 分鐘」 stood still for a whole minute
    if ((eta.samples ?? 0) >= 3) {
      const n = Math.max(10, Math.round(s / 10) * 10);
      return zh ? `約 ${n} 秒` : `About ${n} s`;
    }
    return zh ? "不到 1 分鐘" : "Less than a minute";
  }
  return roughDuration(roundedMinutes(s), zh);
}
