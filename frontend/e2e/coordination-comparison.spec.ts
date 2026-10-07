import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

// 实际持久化开发结果只读验证，不触发模型或重新制造故障。
test("REQ-2005: 真实比较摘要、失败详情、双臂读回与移动布局", async ({
  page,
}) => {
  const accounts = JSON.parse(
    readFileSync(
      new URL("../../local/demo-accounts.json", import.meta.url),
      "utf8",
    ),
  ).accounts;
  const account = accounts.find(
    (a: { username: string }) => a.username === "support_a",
  );
  const mutations: string[] = [];
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("request", (r) => {
    if (r.method() === "POST" && !r.url().endsWith("/login"))
      mutations.push(r.url());
  });
  await page.goto("/");
  await page.getByLabel("用户名", { exact: true }).fill(account.username);
  await page.getByLabel("密码", { exact: true }).fill(account.password);
  await page.getByRole("button", { name: "进入工作台" }).click();
  await page
    .locator(".table-scroll tbody tr")
    .first()
    .getByRole("button", { name: "查看", exact: true })
    .click();
  await page.getByRole("button", { name: "协作任务板", exact: true }).click();
  const panel = page.getByRole("region", {
    name: "单 / 多 Agent 比较",
    exact: true,
  });
  await expect(panel).toBeVisible();
  await expect(panel).toContainText("协议差异未消融");
  await expect(panel).toContainText("输入");
  await expect(panel.locator(".comparison-pair")).toHaveCount(4);
  const rows = panel.locator(".comparison-record");
  await expect(rows).toHaveCount(8);
  await panel.evaluate((element) => {
    element.scrollIntoView({ block: "start" });
    const body = element.closest(".el-dialog__body");
    const tabs = body?.querySelector(".view-tabs");
    if (body && tabs)
      body.scrollTop -= tabs.getBoundingClientRect().height + 24;
  });
  await page.screenshot({
    path: fileURLToPath(
      new URL(
        "../../docs/verification/phase-7-round-3/comparison-desktop.png",
        import.meta.url,
      ),
    ),
  });
  for (const index of [0, 1, 3]) {
    await rows
      .nth(index)
      .getByRole("button", { name: "查看比较记录", exact: true })
      .click();
    await expect(
      page.getByRole("dialog", { name: "比较流程详情", exact: true }),
    ).toContainText("未人工复核");
    if (index !== 0) {
      const original = page.getByRole("dialog", {
        name: "比较流程详情",
        exact: true,
      });
      const read = page.waitForResponse(
        (r) =>
          r.request().method() === "GET" &&
          /\/(evidence\/|citation)/.test(new URL(r.url()).pathname),
      );
      await original
        .getByRole("button", { name: "查看比较原文", exact: true })
        .first()
        .click();
      expect((await read).status()).toBe(200);
      await expect(
        page.getByRole("dialog", { name: "比较证据原文", exact: true }),
      ).toBeVisible();
      await page.keyboard.press("Escape");
      await expect(original).toContainText("未人工复核");
    }
    await page.keyboard.press("Escape");
  }
  await page.getByRole("button", { name: "刷新比较结果", exact: true }).click();
  await expect(panel).toContainText("已收齐");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await panel.evaluate((element) => {
    element.scrollIntoView({ block: "start" });
    const body = element.closest(".el-dialog__body");
    const tabs = body?.querySelector(".view-tabs");
    if (body && tabs)
      body.scrollTop -= tabs.getBoundingClientRect().height + 24;
  });
  await page.screenshot({
    path: fileURLToPath(
      new URL(
        "../../docs/verification/phase-7-round-3/comparison-mobile.png",
        import.meta.url,
      ),
    ),
  });
  await rows
    .nth(1)
    .getByRole("button", { name: "查看比较记录", exact: true })
    .click();
  await expect(
    page.getByRole("dialog", { name: "比较流程详情", exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.keyboard.press("Escape");
  expect(mutations).toEqual([]);
  expect(errors).toEqual([]);
});
