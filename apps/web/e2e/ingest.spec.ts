import { expect, test } from "@playwright/test";
import { clickUntilUrl } from "./helpers";
import fs from "node:fs";
import path from "node:path";
import { apiData, apiPost, TOKEN } from "./api";
import { reseed } from "./seed";

test.skip(!TOKEN, "needs DEV_API_TOKEN");
test.describe.configure({ mode: "serial" });

const SERVICE = 4;
const manifest = fs.readFileSync(path.resolve(__dirname, "../../api/tests/fixtures/requirements_flask.txt"), "utf8");
type Graph = { nodes: { name: string }[] };
type Page = { total: number };

let totalBefore = 0;
let totalAfter = 0;

test.beforeAll(async ({ request }) => {
  test.setTimeout(120_000); // the ingest queries the live OSV API
  totalBefore = (await apiData<Page>(request, "/vulnerabilities?page_size=1")).total;
  const res = await apiPost(request, "/ingest/manifest", {
    service_id: SERVICE,
    ecosystem: "pypi",
    filename: "requirements.txt",
    content: manifest,
  });
  // Advisories come from the live OSV API; without network the ingest itself fails.
  test.skip(res.status() >= 500, `ingest unavailable (${res.status()}), OSV or Mongo unreachable`);
  expect(res.status()).toBe(200);
  expect((await res.json()).data.packages_parsed).toBe(8);
  totalAfter = (await apiData<Page>(request, "/vulnerabilities?page_size=1")).total;
});

test.afterAll(() => reseed());

test("ingested packages appear in the service graph", async ({ page, request }) => {
  const graph = await apiData<Graph>(request, `/services/${SERVICE}/graph`);
  expect(graph.nodes.map((n) => n.name)).toEqual(expect.arrayContaining(["flask", "werkzeug", "jinja2", "requests"]));
  await page.goto(`/services/${SERVICE}`);
  await expect(page.locator(".react-flow__node")).toHaveCount(graph.nodes.length);
  await expect(page.locator(".react-flow__node").filter({ hasText: "werkzeug" })).toBeVisible();
});

test("ingested advisories show up on /vulnerabilities", async ({ page }) => {
  expect(totalAfter).toBeGreaterThan(totalBefore);
  await page.goto("/vulnerabilities");
  await expect(page.getByText(new RegExp(`\\(${totalAfter} total\\)`))).toBeVisible();
});

test("pagination moves between pages and the page lives in the URL", async ({ page }) => {
  test.skip(totalAfter <= 50, "needs more than one page of advisories");
  await page.goto("/vulnerabilities");
  await expect(page.getByText(/Page 1 of/)).toBeVisible();
  await clickUntilUrl(page, page.getByRole("link", { name: "Next" }), /page=2/);
  await expect(page.getByText(/Page 2 of/)).toBeVisible();
  await page.reload();
  await expect(page.getByText(/Page 2 of/)).toBeVisible();
  await clickUntilUrl(page, page.getByRole("link", { name: "Previous" }), /page=1/);
  await expect(page.getByText(/Page 1 of/)).toBeVisible();
});
