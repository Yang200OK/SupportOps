import { closeDetails } from "./ui-navigation";
import { expect, test, type Page } from "@playwright/test";
import { readFileSync, writeFileSync } from "node:fs";

const accounts = JSON.parse(
  readFileSync(
    new URL("../../local/demo-accounts.json", import.meta.url),
    "utf8",
  ),
).accounts as { username: string; password: string }[];
const folder = new URL(
  "../../docs/verification/phase-6-round-3/",
  import.meta.url,
);
async function login(page: Page) {
  const account = accounts.find((a) => a.username === "support_a")!;
  await page.goto("/");
  await page.getByLabel("用户名", { exact: true }).fill(account.username);
  await page.getByLabel("密码", { exact: true }).fill(account.password);
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(
    page.getByRole("heading", { name: "工单工作台", exact: true }),
  ).toBeVisible();
}

test("REQ-1704/1706: 已发布组织方法与当前诊断的真实历史快照读取（零模型）", async ({
  page,
}) => {
  const record = JSON.parse(
    readFileSync(
      new URL("live-publication-plus0728-attempt1.json", folder),
      "utf8",
    ),
  ).attempts[0].response;
  let starts = 0;
  page.on("request", (request) => {
    if (request.method() === "POST" && request.url().includes("investigations"))
      starts++;
  });
  await login(page);
  // 登录后标题先出现，列表异步返回；先等首屏列表再决定是否翻页。
  await expect(page.locator(".ticket-title").first()).toBeVisible();
  let row = page.locator(".table-scroll tbody tr").filter({
    hasText: record.input_snapshot.ticket.ticket_id.slice(0, 8),
  });
  for (let i = 0; i < 40 && !(await row.count()); i++) {
    const next = page
      .getByRole("button", { name: "下一页", exact: true })
      .first();
    const pending = page.waitForResponse(
      (r) =>
        r.request().method() === "GET" &&
        new URL(r.url()).pathname === "/api/tickets",
    );
    await next.click();
    await pending;
    await expect(page.locator(".loading")).toHaveCount(0);
    row = page.locator(".table-scroll tbody tr").filter({
      hasText: record.input_snapshot.ticket.ticket_id.slice(0, 8),
    });
  }
  await row.getByRole("button", { name: "查看", exact: true }).click();
  await page
    .getByRole("dialog", { name: "工单详情", exact: true })
    .getByRole("button", { name: "假设调查", exact: true })
    .click();
  const history = page
    .locator(".hypothesis-history")
    .filter({ hasText: record.investigation_id.slice(0, 12) });
  const response = page.waitForResponse(
    (r) =>
      r.request().method() === "GET" &&
      r.url().endsWith("/api/investigations/" + record.investigation_id),
  );
  await history
    .getByRole("button", { name: "读取历史假设调查", exact: true })
    .click();
  expect(await (await response).json()).toEqual(record);
  await expect(page.locator(".investigation-skills")).toContainText(
    record.skills.loaded[0].body_sha256,
  );
  await expect(page.locator(".investigation-skills")).toContainText(
    record.skills.loaded[0].publication_id,
  );
  await page
    .getByRole("button", { name: "回查假设依据", exact: true })
    .first()
    .click();
  await expect(page.locator(".hypothesis-reference")).toContainText(
    "text_verified",
  );
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  expect(starts).toBe(0);
  writeFileSync(
    new URL(
      "browser-publication-investigation-readback.json",
      new URL(
        "../../docs/verification/ui-refinement-before-phase-7/",
        import.meta.url,
      ),
    ),
    JSON.stringify(
      {
        transport: "real_edge_http",
        investigation_id: record.investigation_id,
        frozen_skill_snapshot_unchanged: true,
        model_post_requests: starts,
      },
      null,
      2,
    ) + "\n",
  );
});
