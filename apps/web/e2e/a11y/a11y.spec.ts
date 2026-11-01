import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "../fixtures";
import { ROUTES } from "../routes";

// a library organized by content: every main route has real content to check
test.use({ scenario: { photos: "library", library: true, organize: "by-content-12" } });

for (const scheme of ["light", "dark"] as const) {
  for (const lang of ["zh-TW", "en"]) {
    test(`[A11Y-001] axe 全路由 0 serious／critical — ${scheme} · ${lang}`, async ({ page, stack }) => {
      test.setTimeout(180_000);
      await page.emulateMedia({ colorScheme: scheme });
      const problems: string[] = [];
      for (const r of ROUTES) {
        await r.open(page, stack, lang);
        const res = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
        for (const v of res.violations.filter((x) => x.impact === "serious" || x.impact === "critical")) {
          problems.push(`${r.name}: ${v.id} (${v.impact}) ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(" | ")}`);
        }
      }
      expect(problems, problems.join("\n")).toEqual([]);
    });
  }
}

test("[A11Y-004] reduced motion：轉場改 ≤ 100 ms 淡入", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.addInitScript(() => {
    const seen: number[] = [];
    (window as unknown as { __durations: number[] }).__durations = seen;
    const orig = Element.prototype.animate;
    Element.prototype.animate = function (k, o) {
      seen.push(typeof o === "number" ? o : Number((o as KeyframeAnimationOptions)?.duration ?? 0));
      return orig.call(this, k, o);
    };
  });
  await page.goto("/");
  await page.getByRole("gridcell").first().click();
  const box = page.getByTestId("lightbox");
  await expect(box).toBeVisible();
  const dur = await box.evaluate((el) => getComputedStyle(el).animationDuration);
  expect(parseFloat(dur) * (dur.endsWith("ms") ? 1 : 1000)).toBeLessThanOrEqual(100);
  expect(await page.evaluate(() => (window as unknown as { __durations: number[] }).__durations.filter((d) => d > 100))).toEqual([]);
});

test("[A11Y-005] forced colors：選取框使用 Highlight", async ({ page }) => {
  await page.emulateMedia({ forcedColors: "active" });
  await page.goto("/search?q=photo");
  const tile = page.getByTestId("search-result").first();
  await tile.click();
  await expect(tile).toHaveAttribute("aria-selected", "true");
  const style = await tile.evaluate((el) => {
    const cs = getComputedStyle(el);
    const probe = document.createElement("div");
    probe.style.color = "Highlight";
    document.body.appendChild(probe);
    const highlight = getComputedStyle(probe).color;
    probe.remove();
    return { outline: cs.outlineStyle, width: cs.outlineWidth, color: cs.outlineColor, highlight };
  });
  expect(style.outline).toBe("solid");
  expect(style.width).toBe("3px");
  expect(style.color).toBe(style.highlight);
});

test("[A11Y-006] 色彩不是唯一資訊：CompatBadge 與狀態都有圖示 + 文字", async ({ page }) => {
  await page.goto("/models");
  const badges = page.getByTestId("compat-badge");
  await expect(badges.first()).toBeVisible();
  for (const b of await badges.all()) {
    const parts = await b.evaluate((el) => ({
      icon: el.querySelector('[aria-hidden="true"]')?.textContent?.trim() ?? "",
      text: (el.textContent ?? "").replace(el.querySelector('[aria-hidden="true"]')?.textContent ?? "", "").trim(),
    }));
    expect(parts.icon).toMatch(/^[●◐○?]$/);
    expect(parts.text.length).toBeGreaterThan(0);
  }
  // the activity indicator: icon + text, and its state in the accessible name
  const act = page.getByRole("button", { name: /^活動：/ });
  await expect(act).toContainText("活動");
  await expect(act.locator('[aria-hidden="true"]')).not.toBeEmpty();
});

test("[I18N-004] 偽語系：字串加長 40% 後主要路由無截斷溢出", async ({ page, stack }) => {
  test.setTimeout(120_000);
  const bad: string[] = [];
  for (const r of ROUTES) {
    await r.open(page, stack, "pseudo");
    const found = await page.evaluate(() => {
      const out: string[] = [];
      if (document.documentElement.scrollWidth > window.innerWidth + 1) out.push(`page scrolls horizontally (${document.documentElement.scrollWidth})`);
      for (const el of Array.from(document.querySelectorAll<HTMLElement>("body *"))) {
        if (!el.childNodes.length || ![...el.childNodes].some((n) => n.nodeType === 3 && n.textContent!.trim())) continue;
        const cs = getComputedStyle(el);
        const clipped = cs.overflowX === "hidden" || cs.overflowX === "clip" || cs.textOverflow === "ellipsis";
        if (clipped && el.scrollWidth > el.clientWidth + 1) out.push(`${el.tagName}.${el.className}: ${el.textContent!.slice(0, 40)}`);
      }
      return out;
    });
    bad.push(...found.map((f) => `${r.name}: ${f}`));
  }
  expect(bad, bad.join("\n")).toEqual([]);
});

test("[I18N-005] 單人模式術語：全路由文字不出現 Worker／Pipeline／dry-run／Workspace／cascade／fingerprint", async ({ page, stack }) => {
  test.setTimeout(120_000);
  const hits: string[] = [];
  for (const lang of ["zh-TW", "en"]) {
    for (const r of ROUTES) {
      await r.open(page, stack, lang);
      const text = await page.evaluate(() => document.body.innerText);
      const m = text.match(/\b(Worker|Pipeline|dry-run|Workspace|cascade|fingerprint)\b/gi);
      if (m) hits.push(`${lang} ${r.name}: ${[...new Set(m)].join(", ")}`);
    }
  }
  expect(hits, hits.join("\n")).toEqual([]);
});
