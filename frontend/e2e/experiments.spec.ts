import { closeDetails, openImport } from "./ui-navigation";
import { expect, test, type Page } from "@playwright/test";
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
const accounts = JSON.parse(
  readFileSync(
    new URL("../../local/demo-accounts.json", import.meta.url),
    "utf8",
  ),
).accounts as { username: string; password: string }[];
const evidence = new URL(
  process.env.SUPPORTOPS_EXPERIMENT_EVIDENCE_PATH ??
    "../../docs/verification/ui-refinement-before-phase-7/",
  import.meta.url,
);
async function login(page: Page, name: string) {
  const account = accounts.find((a) => a.username === name)!;
  await page.goto("/");
  await page.getByLabel("用户名", { exact: true }).fill(account.username);
  await page.getByLabel("密码", { exact: true }).fill(account.password);
  await page.getByRole("button", { name: "进入工作台" }).click();
  await page.getByRole("button", { name: /运行记录/ }).click();
  await expect(page.getByRole("heading", { name: /运行历史/ })).toBeVisible();
  await page.getByRole("button", { name: "故障实验", exact: true }).click();
}
test("REQ-508: 三阶段、原始观测、分页、视图和会话隔离", async ({ page }) => {
  const errors: string[] = [],
    checks: string[] = [];
  page.on("pageerror", (e) => errors.push(e.name));
  await login(page, "support_a");
  await expect(page.locator(".experiments-panel tbody tr")).toHaveCount(10);
  await page
    .locator(".experiments-panel tbody tr")
    .first()
    .getByRole("button", { name: /查看观测/ })
    .click();
  await expect(
    page.getByRole("dialog", { name: "三阶段观测", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".phase-summaries")).toContainText("正常请求");
  await expect(page.locator(".phase-summaries")).toContainText("异常观测");
  await expect(page.locator(".phase-summaries")).toContainText("恢复复测");
  await expect(page.locator(".phase-summaries")).toContainText("RD_TIMEOUT");
  await expect(page.locator(".observation:visible").first()).toContainText(
    "lab:",
  );
  await page.getByText("实际生效配置", { exact: true }).first().click();
  await expect(page.locator(".experiment-detail")).toContainText(
    "downstream_timeout_ms",
  );
  const original = await page.locator(".experiment-detail").textContent();
  expect(original).not.toContain("root_cause");
  expect(original).not.toContain("fault_family");
  expect(original).not.toContain("holdout");
  checks.push(
    "three_phases_error_and_actual_timing_config_evidence_ids_no_labels",
  );
  await page.locator(".experiment-detail").screenshot({
    path: fileURLToPath(new URL("experiment-desktop.png", evidence)),
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.locator(".phase-summaries").scrollIntoViewIfNeeded();
  await page.screenshot({
    path: fileURLToPath(new URL("experiment-mobile.png", evidence)),
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  checks.push("desktop_and_mobile_no_horizontal_overflow");
  await closeDetails(page);
  await page.getByRole("button", { name: "下一页观测", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "上一页观测", exact: true }),
  ).toBeEnabled();
  expect(await page.locator(".experiment-detail").count()).toBe(0);
  await page.getByRole("button", { name: "下一页观测", exact: true }).click();
  await expect(page.locator(".experiments-panel tbody tr")).toHaveCount(4);
  await page
    .locator(".experiments-panel tbody tr")
    .first()
    .getByRole("button", { name: /查看观测/ })
    .click();
  await expect(page.locator(".phase-summaries")).toContainText(
    "RD_CONFIG_INVALID",
  );
  await expect(page.locator(".phase-summaries")).toContainText("启动校验");
  checks.push("pagination_and_real_failed_startup_distinguished_from_http");
  await closeDetails(page);
  await page.getByRole("button", { name: "输入检查", exact: true }).click();
  await expect(page.getByRole("heading", { name: /运行历史/ })).toBeVisible();
  expect(await page.locator(".experiment-detail").count()).toBe(0);
  await page.getByRole("button", { name: "故障实验", exact: true }).click();
  await page
    .locator(".experiments-panel tbody tr")
    .first()
    .getByRole("button", { name: /查看观测/ })
    .click();
  await expect(page.locator(".experiment-detail")).toBeVisible();
  checks.push("default_intake_view_preserved_and_switch_clears_observations");
  const token = await page.evaluate(() =>
    sessionStorage.getItem("supportops.session.v1"),
  );
  expect(
    (
      await page.request.post("http://127.0.0.1:8010/api/auth/logout", {
        headers: { Authorization: `Bearer ${token}` },
      })
    ).status(),
  ).toBe(204);
  await closeDetails(page);
  await page.getByRole("button", { name: "刷新观测", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "登录支持工作台" }),
  ).toBeVisible();
  expect(await page.locator(".experiments-panel").count()).toBe(0);
  await login(page, "support_b");
  await expect(
    page.getByRole("heading", { name: "暂无实验观测" }),
  ).toBeVisible();
  expect(await page.locator(".experiment-detail").count()).toBe(0);
  checks.push("expired_session_and_cross_org_clear_private_records");
  expect(errors).toEqual([]);
  writeFileSync(
    new URL("experiments-browser.json", evidence),
    JSON.stringify(
      {
        browser: "msedge",
        transport: "real_http",
        checks,
        pageerror: errors,
        model_calls: 0,
      },
      null,
      2,
    ) + "\n",
    "utf8",
  );
});
