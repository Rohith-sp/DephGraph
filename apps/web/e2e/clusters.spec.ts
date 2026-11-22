import { expect, test } from "@playwright/test";
import { apiData, TOKEN } from "./api";

type Member = { x: number | null; y: number | null };
type Cluster = { cluster_id: number; label: string; members: Member[] };

test.skip(!TOKEN, "needs DEV_API_TOKEN");

test("legend lists every cluster; scatter shows when coordinates exist, else an empty state", async ({ page, request }) => {
  const clusters = await apiData<Cluster[]>(request, "/clusters");
  const members = clusters.flatMap((c) => c.members);
  await page.goto("/clusters");

  const legend = page.getByRole("list", { name: "Cluster legend" });
  await expect(legend.getByRole("listitem")).toHaveCount(clusters.length);
  for (const c of clusters) await expect(legend).toContainText(c.label);

  if (members.some((m) => m.x != null && m.y != null)) {
    await expect(page.getByRole("img", { name: "Package clusters, 2D projection" })).toBeVisible();
  } else {
    await expect(page.getByText("No 2D projection yet.")).toBeVisible();
  }
});

test("redundant packages are listed per service", async ({ page }) => {
  await page.goto("/clusters");
  const section = page.getByRole("region", { name: "Redundant packages" });
  await expect(section.getByRole("listitem").first()).toContainText("packages in");
});
