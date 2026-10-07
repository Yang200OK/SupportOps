import { expect, test } from "@playwright/test";
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const folder = new URL(
  "../../docs/verification/phase-7-round-2/",
  import.meta.url,
);
const accounts = JSON.parse(
  readFileSync(
    new URL("../../local/demo-accounts.json", import.meta.url),
    "utf8",
  ),
).accounts;

test("REQ-1901/1905/1906: 页面真实创建执行、取消与持久化（零模型）", async ({
  page,
}) => {
  const proof = JSON.parse(
    readFileSync(new URL("browser-startup-prepare.json", folder), "utf8"),
  );
  let models = 0;
  page.on("request", (r) => {
    if (r.method() === "POST" && r.url().endsWith("/advance")) models++;
  });
  const account = accounts.find(
    (a: { username: string }) => a.username === "support_a",
  );
  await page.goto("/");
  await page.getByLabel("用户名", { exact: true }).fill(account.username);
  await page.getByLabel("密码", { exact: true }).fill(account.password);
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(page.locator(".ticket-title").first()).toBeVisible();
  await page.getByRole("button", { name: "新建工单" }).click();
  await page.getByLabel("问题标题").fill(`协作执行创建验收 ${Date.now()}`);
  await page
    .getByLabel("问题描述")
    .fill("RelayDesk 1.1 启动校验 RD_CONFIG_INVALID，分别核对资料与本次诊断。");
  await page.locator(".ticket-form select").first().selectOption("1.1");
  await page.getByRole("button", { name: "创建工单", exact: true }).click();
  await page.getByRole("button", { name: "协作任务板", exact: true }).click();
  await page.getByLabel("协作本次实验").selectOption(proof.lab_run_id);
  await page.getByRole("button", { name: "创建协作计划", exact: true }).click();
  const created = page.waitForResponse(
    (r) => r.request().method() === "POST" && r.url().endsWith("/executions"),
  );
  await page.getByRole("button", { name: "创建协作执行", exact: true }).click();
  const body = await (await created).json();
  expect(body.status).toBe("pending");
  const cancelled = page.waitForResponse(
    (r) => r.request().method() === "POST" && r.url().endsWith("/cancel"),
  );
  await page.getByRole("button", { name: "取消协作执行", exact: true }).click();
  const final = await (await cancelled).json();
  expect(final.status).toBe("cancelled");
  expect(final.usage.model_calls).toBe(0);
  await expect(page.locator(".execution-panel")).toContainText(
    "执行状态：已取消",
  );
  const read = page.waitForResponse(
    (r) =>
      new URL(r.url()).pathname ===
      `/api/coordination-executions/${final.execution_id}`,
  );
  await page.getByRole("button", { name: "刷新执行", exact: true }).click();
  expect(await (await read).json()).toEqual(final);
  expect(models).toBe(0);
  writeFileSync(
    new URL("browser-cancelled-execution.json", folder),
    JSON.stringify(
      { execution_id: final.execution_id, final, model_calls: 0 },
      null,
      2,
    ),
  );
});

test("REQ-1906: 真实多 Agent 结果、来源、刷新读回与移动布局（零模型）", async ({
  page,
}) => {
  const proof = JSON.parse(
    readFileSync(new URL("live-configuration-attempt2.json", folder), "utf8"),
  );
  const record = proof.final;
  expect(record.status).toBe("completed");
  const errors: string[] = [];
  const calls: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("request", (r) => {
    if (
      r.method() === "POST" &&
      /\/(advance|executions)$/.test(new URL(r.url()).pathname)
    )
      calls.push(r.url());
  });
  const account = accounts.find(
    (a: { username: string }) => a.username === "support_a",
  );
  await page.goto("/");
  await page.getByLabel("用户名", { exact: true }).fill(account.username);
  await page.getByLabel("密码", { exact: true }).fill(account.password);
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(page.locator(".ticket-title").first()).toBeVisible();
  let row = page
    .locator(".table-scroll tbody tr")
    .filter({ hasText: proof.board.ticket_id.slice(0, 8) });
  for (let n = 0; n < 40 && !(await row.count()); n++) {
    const loaded = page.waitForResponse(
      (r) =>
        r.request().method() === "GET" &&
        new URL(r.url()).pathname === "/api/tickets",
    );
    await page
      .getByRole("button", { name: "下一页", exact: true })
      .first()
      .click();
    await loaded;
    await expect(page.locator(".loading")).toHaveCount(0);
    row = page
      .locator(".table-scroll tbody tr")
      .filter({ hasText: proof.board.ticket_id.slice(0, 8) });
  }
  await row.getByRole("button", { name: "查看", exact: true }).click();
  await page.getByRole("button", { name: "协作任务板", exact: true }).click();
  await page.getByRole("button", { name: "查看计划", exact: true }).click();
  await page.getByRole("button", { name: "读取执行历史", exact: true }).click();
  const history = page
    .locator(".execution-task")
    .filter({ hasText: record.execution_id.slice(0, 8) });
  const loaded = page.waitForResponse(
    (r) =>
      new URL(r.url()).pathname ===
      `/api/coordination-executions/${record.execution_id}`,
  );
  await history
    .getByRole("button", { name: "查看协作执行", exact: true })
    .click();
  expect(await (await loaded).json()).toEqual(record);
  await expect(page.locator(".execution-panel")).toContainText(
    "执行状态：已完成",
  );
  await page.getByRole("button", { name: "查看归并报告", exact: true }).click();
  await page
    .getByRole("button", { name: "查看协作原文", exact: true })
    .first()
    .click();
  const source = page.getByRole("dialog", {
    name: "协作证据原文",
    exact: true,
  });
  await expect(source).toContainText('"text_verified": true');
  await page.keyboard.press("Escape");
  await expect(source).not.toBeVisible();
  await page.screenshot({
    path: fileURLToPath(new URL("execution-desktop.png", folder)),
    animations: "disabled",
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: fileURLToPath(new URL("execution-mobile.png", folder)),
    animations: "disabled",
  });
  const refreshed = page.waitForResponse(
    (r) =>
      new URL(r.url()).pathname ===
      `/api/coordination-executions/${record.execution_id}`,
  );
  await page.getByRole("button", { name: "刷新执行", exact: true }).click();
  expect(await (await refreshed).json()).toEqual(record);
  expect(calls).toEqual([]);
  expect(errors).toEqual([]);
  writeFileSync(
    new URL("browser-execution-readback.json", folder),
    JSON.stringify(
      {
        execution_id: record.execution_id,
        state_sha256: record.state_sha256,
        errors,
        model_calls: 0,
        layout: "desktop and 390px",
      },
      null,
      2,
    ),
  );
});
