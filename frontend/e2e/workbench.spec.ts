import { closeDetails } from "./ui-navigation";
import { test, expect, type Page } from "@playwright/test";
import { readFileSync, writeFileSync } from "node:fs";

// 只在本机读取演示账号；不写进公开报告，不保存含密码的 trace。
const accounts: { username: string; password: string }[] = JSON.parse(
  readFileSync(
    new URL("../../local/demo-accounts.json", import.meta.url),
    "utf8",
  ),
).accounts;
const root = new URL(
  process.env.SUPPORTOPS_BROWSER_EVIDENCE_PATH ??
    "../../docs/verification/phase-1-round-3/",
  import.meta.url,
);
const checks: string[] = [];
async function login(page: Page, username: string) {
  const account = accounts.find((item) => item.username === username);
  if (!account) throw new Error("本地演示账号不存在。");
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "登录支持工作台" }),
  ).toBeVisible();
  await page
    .getByRole("textbox", { name: "用户名", exact: true })
    .fill(account.username);
  await page.getByLabel("密码", { exact: true }).fill(account.password);
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(
    page.getByRole("heading", { name: "工单工作台", exact: true }),
  ).toBeVisible();
  await expect(page.getByText("数据库已就绪")).toBeVisible();
}
test("REQ-201/202/203/205/209: 真实浏览器登录、持久化、检查、分页、隔离与评测", async ({
  page,
  context,
}) => {
  const consoleErrors: string[] = [];
  page.on("pageerror", (error) => consoleErrors.push(error.name));
  await login(page, "support_a");
  checks.push("browser_login_database_ready");
  const title = `浏览器构造案例 ${Date.now()}`;
  await page.getByRole("button", { name: "+ 新建工单" }).click();
  await page.getByLabel("问题标题", { exact: true }).fill(title);
  const untrusted =
    '<img src=x onerror="window.__supportops_xss=1"> RD_TIMEOUT，无日志。';
  await page.getByLabel("问题描述", { exact: true }).fill(untrusted);
  await page.getByRole("button", { name: "创建工单", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: title, exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("需要补充产品版本。当前输入不足以开始调查。"),
  ).toBeVisible();
  await expect(page.locator(".description")).toHaveText(untrusted);
  expect(
    await page.evaluate(() =>
      Boolean((window as unknown as Record<string, unknown>).__supportops_xss),
    ),
  ).toBe(false);
  expect(await page.locator(".description img").count()).toBe(0);
  checks.push("create_unknown_version_and_escape_html");
  await page.getByRole("button", { name: "记录一次输入检查" }).click();
  await expect(page.locator(".run-detail .badge")).toHaveText("blocked");
  await expect(page.locator(".events li")).toHaveCount(3);
  await expect(page.locator(".events")).toContainText("ms");
  const runId = await page.locator(".run-meta .mono").first().innerText();
  const ticketId = await page.locator(".detail-card dd.mono").innerText();
  checks.push("blocked_run_three_persisted_events");
  await closeDetails(page);
  await page.getByRole("button", { name: /02\s*运行记录/ }).click();
  await expect(
    page.getByRole("heading", { name: "运行记录", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "查看记录" }).first().click();
  await expect(page.locator(".run-detail")).toContainText(runId);
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "工单工作台", exact: true }),
  ).toBeVisible();
  checks.push("reload_restores_current_tab_session");
  // 同一真实会话创建分页所需的构造工单，避免写入真实用户内容。
  await page.evaluate(async () => {
    const token = sessionStorage.getItem("supportops.session.v1");
    for (let index = 0; index < 10; index++) {
      const response = await fetch("/api/tickets", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          title: `分页构造案例 ${index + 1}`,
          description: "只验证分页，RD_TIMEOUT，尚无日志。",
          product: "relaydesk",
          product_version: "1.1",
          environment: "local_lab",
          source_type: "synthetic_case",
        }),
      });
      if (response.status !== 201) throw new Error("分页构造数据创建失败。");
    }
  });
  await page.getByRole("button", { name: "刷新", exact: true }).click();
  await expect(page.locator("tbody tr")).toHaveCount(10);
  await page.getByRole("button", { name: "下一页" }).click();
  await expect(page.locator(".pagination>div>span")).toHaveText("2");
  await expect(page.locator("tbody")).toContainText(title);
  checks.push("real_ticket_pagination_second_page");
  await page
    .locator("tbody tr")
    .filter({ hasText: title })
    .getByRole("button", { name: "查看" })
    .click();
  await page.getByRole("button", { name: "记录一次输入检查" }).click();
  await page.screenshot({
    path: new URL(
      "regression-workbench-workbench-desktop.png",
      new URL(
        "../../docs/verification/ui-refinement-before-phase-7/",
        import.meta.url,
      ),
    ).pathname.replace(/^\/(\w:)/, "$1"),
    fullPage: true,
  });
  await closeDetails(page);
  await page.getByRole("button", { name: /03\s*评测准备/ }).click();
  await page.getByRole("button", { name: "编辑与校验数据集" }).click();
  await page.getByRole("button", { name: "校验数据集格式" }).click();
  await expect(page.getByText("格式通过", { exact: true })).toBeVisible();
  await expect(
    page.getByText("executed: false · 未执行质量评测"),
  ).toBeVisible();
  const editor = page.getByLabel("评测数据集 JSON");
  const valid = JSON.parse(await editor.inputValue());
  valid.tasks[1].source_group = valid.tasks[0].source_group;
  await editor.fill(JSON.stringify(valid));
  await page.getByRole("button", { name: "校验数据集格式" }).click();
  await expect(
    page
      .getByRole("dialog", { name: "数据集格式校验", exact: true })
      .getByRole("alert"),
  ).toContainText("输入契约");
  await expect(page.getByText("格式通过", { exact: true })).toHaveCount(0);
  checks.push("dataset_valid_and_cross_split_family_rejected");
  await closeDetails(page);
  await page.getByRole("button", { name: "退出登录" }).click();
  await expect(
    page.getByRole("heading", { name: "登录支持工作台" }),
  ).toBeVisible();
  // 刷新时登录框可先于认证响应出现；等待撤销处理完成，并避免失败日志回显 token。
  await expect
    .poll(async () =>
      page.evaluate(
        () => sessionStorage.getItem("supportops.session.v1") === null,
      ),
    )
    .toBe(true);
  await login(page, "support_b");
  await expect(page.locator("main")).not.toContainText(title);
  const boundary = await page.evaluate(
    async ({ ticketId, runId }) => {
      const headers = {
        Authorization: `Bearer ${sessionStorage.getItem("supportops.session.v1")}`,
      };
      return [
        (await fetch(`/api/tickets/${ticketId}`, { headers })).status,
        (await fetch(`/api/runs/${runId}`, { headers })).status,
      ];
    },
    { ticketId, runId },
  );
  expect(boundary).toEqual([404, 404]);
  checks.push("logout_clear_and_other_org_hidden_404");
  await closeDetails(page);
  await page.getByRole("button", { name: "退出登录" }).click();
  await login(page, "support_a2");
  await page.getByRole("button", { name: "下一页" }).click();
  await expect(page.locator("tbody")).toContainText(title);
  checks.push("same_org_colleague_can_read");
  // 浏览器无凭据的新标签页必须进入登录页。
  const fresh = await context.newPage();
  await fresh.goto("/");
  await expect(
    fresh.getByRole("heading", { name: "登录支持工作台" }),
  ).toBeVisible();
  await fresh.close();
  checks.push("new_tab_without_session_requires_login");
  // 服务端撤销后刷新：API 返回 401，旧组织页面被清除。
  await page.evaluate(async () => {
    await fetch("/api/auth/logout", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${sessionStorage.getItem("supportops.session.v1")}`,
      },
    });
  });
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "登录支持工作台" }),
  ).toBeVisible();
  // 会话清除异步完成；只断言布尔值，失败日志不回显凭据。
  await expect
    .poll(() =>
      page.evaluate(() =>
        Boolean(sessionStorage.getItem("supportops.session.v1")),
      ),
    )
    .toBe(false);
  checks.push("server_revoked_session_401_clears_ui");
  expect(consoleErrors).toEqual([]);
  await login(page, "support_a");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: new URL(
      "regression-workbench-workbench-mobile.png",
      new URL(
        "../../docs/verification/ui-refinement-before-phase-7/",
        import.meta.url,
      ),
    ).pathname.replace(/^\/(\w:)/, "$1"),
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  checks.push("mobile_390px_no_page_overflow_no_pageerror");
  await closeDetails(page);
  await page.getByRole("button", { name: "退出登录" }).click();
  writeFileSync(
    new URL(
      "browser.json",
      new URL(
        "../../docs/verification/ui-refinement-before-phase-7/",
        import.meta.url,
      ),
    ),
    JSON.stringify(
      {
        executed_at_utc: new Date().toISOString(),
        transport: "real_edge_browser_real_http_postgresql",
        status: "passed",
        checks,
        console_errors: consoleErrors,
        not_run: ["agent_investigation", "rag_quality_evaluation"],
      },
      null,
      2,
    ) + "\n",
    "utf8",
  );
});
