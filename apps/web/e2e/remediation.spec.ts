import { expect, test } from "@playwright/test";
import { hydrated } from "./helpers";
import { apiData, TOKEN } from "./api";

type Step = { step: number; cumulative_coverage_pct: number };

test.skip(!TOKEN, "needs DEV_API_TOKEN");

test("picking a team renders its plan, coverage ends at the maximum", async ({ page, request }) => {
  await page.goto("/remediation");
  await page.getByLabel("Team").selectOption({ label: "Payments" });
  await page.getByRole("button", { name: "Show plan" }).click();
  await expect(page).toHaveURL(/team_id=2/);

  const steps = await apiData<Step[]>(request, "/remediation/plan/2");
  const body = page.getByRole("table").getByRole("row").filter({ has: page.getByRole("cell") });
  await expect(body).toHaveCount(steps.length);

  const shown = (await body.locator("td:last-child").allTextContents()).map((t) => parseFloat(t));
  expect(shown).toEqual([...shown].sort((a, b) => a - b));
  expect(shown.at(-1)).toBeCloseTo(Math.max(...steps.map((s) => s.cumulative_coverage_pct)), 1);
  expect(shown.at(-1)).toBeLessThanOrEqual(100);
  await expect(page.getByRole("img", { name: "Cumulative vulnerability coverage per remediation step" })).toBeVisible();
});

test("save the plan and tick actions off", async ({ page, request }) => {
  const steps = await apiData<Step[]>(request, "/remediation/plan/1");
  await page.goto("/remediation");
  await (await hydrated(page.getByRole("button", { name: "Save plan" }))).click();
  await expect(page.getByText("Plan saved")).toBeVisible();

  const progress = page.getByRole("progressbar", { name: "Plan progress" });
  await expect(progress).toHaveAttribute("aria-valuenow", "0");
  await expect(page.getByText(`0 of ${steps.length} actions done (0%)`)).toBeVisible();

  await (await hydrated(page.getByRole("button", { name: "Mark done action 1" }))).click();
  await expect(page.getByText(`1 of ${steps.length} actions done`)).toBeVisible();
  await expect(progress).toHaveAttribute("aria-valuenow", String(Math.round(100 / steps.length)));
  await expect(page.getByRole("button", { name: "Undo action 1" })).toBeVisible();

  await (await hydrated(page.getByRole("button", { name: "Undo action 1" }))).click();
  await expect(page.getByText(`0 of ${steps.length} actions done`)).toBeVisible();
});

test("a saved plan and its done actions survive a reload", async ({ page }) => {
  await page.goto("/remediation");
  await (await hydrated(page.getByRole("button", { name: "Save plan" }))).click();
  await expect(page.getByText("Plan saved")).toBeVisible();
  await (await hydrated(page.getByRole("button", { name: "Mark done action 1" }))).click();
  await expect(page.getByRole("button", { name: "Undo action 1" })).toBeVisible();

  await page.reload();
  await expect(page.getByRole("button", { name: "Undo action 1" })).toBeVisible();
  await expect(page.getByRole("progressbar", { name: "Plan progress" })).not.toHaveAttribute("aria-valuenow", "0");
});

test("an invalid team id falls back to the first team", async ({ page }) => {
  await page.goto("/remediation?team_id=1.5");
  await expect(page.locator("main").getByRole("alert")).toHaveCount(0);
  await expect(page.getByLabel("Team")).toHaveValue("1");
  await expect(page.getByRole("table")).toBeVisible();
});
