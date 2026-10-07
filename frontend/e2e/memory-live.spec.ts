import { closeDetails } from "./ui-navigation";
import { expect, test } from "@playwright/test";
import { readFileSync, writeFileSync } from "node:fs";

const folder = new URL(
  "../../docs/verification/phase-6-round-2/",
  import.meta.url,
);
const accounts = JSON.parse(
  readFileSync(
    new URL("../../local/demo-accounts.json", import.meta.url),
    "utf8",
  ),
).accounts as { username: string; password: string }[];

test("REQ-1606: 真实事件复盘、原文回查和撤销，桌面与手机，零模型", async ({
  page,
}) => {
  const source = JSON.parse(
    readFileSync(
      new URL("../phase-6-round-1/live-configuration-attempt2.json", folder),
      "utf8",
    ),
  ).response;
  const account = accounts.find((a) => a.username === "support_a")!;
  let modelPosts = 0;
  page.on("request", (r) => {
    if (r.method() === "POST" && r.url().includes("hypothesis-investigations"))
      modelPosts++;
  });
  await page.goto("/");
  await page.getByLabel("用户名", { exact: true }).fill(account.username);
  await page.getByLabel("密码", { exact: true }).fill(account.password);
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(page.locator(".ticket-title").first()).toBeVisible();
  const row = page.locator(".table-scroll tbody tr").filter({
    has: page.getByText(source.input_snapshot.ticket.title, { exact: true }),
  });
  for (let i = 0; i < 40 && !(await row.count()); i++) {
    const pending = page.waitForResponse(
      (r) =>
        r.request().method() === "GET" &&
        new URL(r.url()).pathname === "/api/tickets",
    );
    await page
      .getByRole("button", { name: "下一页", exact: true })
      .first()
      .click();
    await pending;
    await expect(page.locator(".loading")).toHaveCount(0);
  }
  await row.getByRole("button", { name: "查看", exact: true }).click();
  await page
    .getByRole("dialog", { name: "工单详情", exact: true })
    .getByRole("button", { name: "假设调查", exact: true })
    .click();
  await expect(
    page.getByLabel("参考可召回经验候选（待审核）"),
  ).not.toBeChecked();
  await page
    .locator(".hypothesis-history")
    .filter({ hasText: source.investigation_id.slice(0, 12) })
    .getByRole("button")
    .click();
  await page.getByRole("button", { name: "准备事件复盘" }).click();
  await expect(page.getByLabel("复盘绑定动作")).toHaveValue("");
  const created = page.waitForResponse(
    (r) =>
      r.request().method() === "POST" && r.url().endsWith("/retrospectives"),
  );
  await page.getByRole("button", { name: "生成摘录摘要与候选" }).click();
  const response = await created;
  expect(response.status()).toBe(201);
  const record = await response.json();
  expect(record.model_calls).toBe(0);
  await expect(page.getByRole("heading", { name: "复盘已保存" })).toBeVisible();
  await page.getByRole("button", { name: "经验与 Skill" }).click();
  await page.getByRole("button", { name: "事件复盘", exact: true }).click();
  const card = page
    .locator(".memory-card")
    .filter({ hasText: record.retrospective_id.slice(0, 12) });
  await card.getByRole("button", { name: "查看复盘与来源" }).click();
  await expect(page.locator(".memory-detail")).toContainText(
    record.source_sha256,
  );
  await page
    .locator(".memory-detail")
    .getByRole("button", { name: /读取历史原文/ })
    .first()
    .click();
  await expect(page.locator(".memory-detail")).toContainText(
    '"text_verified": true',
  );
  await page
    .getByLabel("经验治理原因")
    .fill("浏览器验收候选撤销，停止后续召回");
  await page.getByRole("button", { name: "撤销经验", exact: true }).click();
  await expect(page.locator(".memory-detail")).toContainText("已撤销 · 修订 2");
  await page
    .getByRole("heading", { name: "冻结摘录摘要" })
    .scrollIntoViewIfNeeded();
  await page.screenshot({
    path: new URL(
      "regression-memory-live-memory-desktop.png",
      new URL(
        "../../docs/verification/ui-refinement-before-phase-7/",
        import.meta.url,
      ),
    ).pathname.replace(/^\//, ""),
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page
    .getByRole("heading", { name: "冻结摘录摘要" })
    .scrollIntoViewIfNeeded();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: new URL(
      "regression-memory-live-memory-mobile.png",
      new URL(
        "../../docs/verification/ui-refinement-before-phase-7/",
        import.meta.url,
      ),
    ).pathname.replace(/^\//, ""),
  });
  const reread = page.waitForResponse(
    (r) =>
      r.request().method() === "GET" &&
      r.url().includes(`/api/retrospectives/${record.retrospective_id}?`),
  );
  await card.getByRole("button", { name: "查看复盘与来源" }).click();
  const final = await (await reread).json();
  expect(final.source_sha256).toBe(record.source_sha256);
  expect(final.candidate.status).toBe("revoked");
  expect(modelPosts).toBe(0);
  writeFileSync(
    new URL(
      "browser-lifecycle.json",
      new URL(
        "../../docs/verification/ui-refinement-before-phase-7/",
        import.meta.url,
      ),
    ),
    JSON.stringify(
      {
        records: [final],
        transport: "real_edge_http",
        checks: "passed",
        model_calls: 0,
        source_unchanged: true,
        mobile_no_overflow: true,
      },
      null,
      2,
    ) + "\n",
  );
});
