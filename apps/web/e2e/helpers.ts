import { expect, type Locator, type Page } from "@playwright/test";

// React tags a DOM node with __reactProps$… once it is hydrated. Next's router drops a click that
// lands earlier, so interact only after this resolves.
export async function hydrated(target: Locator): Promise<Locator> {
  await target.waitFor();
  await expect.poll(() => target.evaluate((el) => Object.keys(el).some((k) => k.startsWith("__reactProps")))).toBe(true);
  return target;
}

// Soft navigations from a Link are occasionally dropped while Next's router is still settling after a
// load or reload. Click again until the URL changes; every caller's link is safe to click twice.
export async function clickUntilUrl(page: Page, target: Locator, url: RegExp) {
  await expect(async () => {
    await target.click({ timeout: 2000 });
    await expect(page).toHaveURL(url, { timeout: 2000 });
  }).toPass();
}
