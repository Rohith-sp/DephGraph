import { expect, test } from "@playwright/test";
import { hydrated } from "./helpers";
import { apiData, TOKEN } from "./api";

type Graph = { nodes: { name: string; version: string; vuln_count: number }[]; edges: unknown[] };

test.skip(!TOKEN, "needs DEV_API_TOKEN");

test("every package is a node and vulnerable ones are tinted", async ({ page, request }) => {
  const graph = await apiData<Graph>(request, "/services/1/graph");
  await page.goto("/services/1");
  const nodes = page.locator(".react-flow__node");
  await expect(nodes).toHaveCount(graph.nodes.length);
  await expect(page.locator(".react-flow__edge")).toHaveCount(graph.edges.length);

  const tinted = nodes.locator("div.w-\\[208px\\]").filter({ has: page.locator("span.rounded-full") });
  await expect(tinted).toHaveCount(graph.nodes.filter((n) => n.vuln_count > 0).length);
  await expect(nodes.filter({ hasText: "aiohttp" }).locator("div.w-\\[208px\\]")).not.toHaveClass(/bg-(crit|high|med|low)\/10/);
  await expect(nodes.filter({ hasText: "jinja2" }).filter({ hasText: "1.2.6" }).locator("div.w-\\[208px\\]")).toHaveClass(
    /bg-high\/10/,
  );
});

test("selecting a package shows its dependency path", async ({ page }) => {
  await page.goto("/services/1");
  const paths = page.getByRole("list", { name: /^Paths to / });
  await (await hydrated(page.getByRole("button", { name: /pyyaml 4\.3\.9/ }))).click();
  await expect(page.getByRole("button", { name: /pyyaml 4\.3\.9/ })).toHaveAttribute("aria-pressed", "true");
  const chain = paths.getByRole("listitem").first();
  await expect(chain).toContainText("api-gateway");
  await expect(chain).toContainText("pyyaml 4.3.9");
  await expect(chain).toContainText("jinja2 1.3.4"); // a transitive hop between the service and pyyaml

  await (await hydrated(page.locator(".react-flow__node").filter({ hasText: "flask" }).first())).click();
  await expect(page.getByRole("button", { name: /flask 3\.1\.4/ })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("list", { name: "Paths to flask" })).toContainText("api-gateway");
});

test("zoom controls change the viewport", async ({ page }) => {
  await page.goto("/services/1");
  const viewport = page.locator(".react-flow__viewport");
  const before = await viewport.getAttribute("style");
  await (await hydrated(page.getByRole("button", { name: "Zoom in" }))).click();
  await expect.poll(() => viewport.getAttribute("style")).not.toBe(before);
});
