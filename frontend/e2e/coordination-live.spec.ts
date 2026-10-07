import { expect, test } from "@playwright/test";
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const evidence = new URL(
  "../../docs/verification/phase-7-round-1/",
  import.meta.url,
);
const accounts = JSON.parse(
  readFileSync(
    new URL("../../local/demo-accounts.json", import.meta.url),
    "utf8",
  ),
).accounts as { username: string; password: string }[];
const registration = JSON.parse(
  readFileSync(new URL("live-startup-prepare.json", evidence), "utf8"),
);

test("REQ-1801/1805/1806: 真实 HTTP 创建、私有包、取消与刷新读回", async ({
  page,
}) => {
  const errors: string[] = [];
  const forbidden: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("request", (r) => {
    if (
      r.method() === "POST" &&
      /\/(hypothesis-investigations|investigations|answers|action-jobs)(\/|$)/.test(
        new URL(r.url()).pathname,
      )
    )
      forbidden.push(r.url());
  });
  const a = accounts.find((a) => a.username === "support_a")!;
  await page.goto("/");
  await page.getByLabel("用户名", { exact: true }).fill(a.username);
  await page.getByLabel("密码", { exact: true }).fill(a.password);
  await page.getByRole("button", { name: "进入工作台" }).click();
  await page.getByRole("button", { name: "新建工单" }).click();
  const title = `第7阶段调度基础验收 ${Date.now()}`;
  await page.getByLabel("问题标题").fill(title);
  await page
    .getByLabel("问题描述")
    .fill(
      "RelayDesk 1.1 启动失败，RD_CONFIG_INVALID，核对版本资料与本次启动诊断。",
    );
  await page.locator(".ticket-form select").first().selectOption("1.1");
  await page.getByRole("button", { name: "创建工单", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "工单详情", exact: true });
  await dialog.getByRole("button", { name: "协作任务板", exact: true }).click();
  await page.getByLabel("协作本次实验").selectOption(registration.lab_run_id);
  const saved = page.waitForResponse(
    (r) =>
      r.request().method() === "POST" && /\/coordination-boards$/.test(r.url()),
  );
  await page.getByRole("button", { name: "创建协作计划", exact: true }).click();
  const response = await saved;
  expect(response.status()).toBe(201);
  const planned = await response.json();
  expect(planned.tasks.map((t: { status: string }) => t.status)).toEqual([
    "ready",
    "ready",
    "blocked",
  ]);
  await expect(page.locator(".coordination-panel")).toContainText(
    "实际调用 0 次",
  );
  const privateResponse = page.waitForResponse((r) =>
    r.url().endsWith("/tasks/documents/package"),
  );
  await page
    .getByRole("button", { name: "查看任务包", exact: true })
    .first()
    .click();
  const privatePackage = await (await privateResponse).json();
  const task = page.getByRole("dialog", { name: "私有任务包", exact: true });
  await expect(task).toContainText("search_knowledge");
  await expect(task).not.toContainText("read_startup_diagnostic");
  await page.keyboard.press("Escape");
  await expect(task).not.toBeVisible();
  await page.screenshot({
    path: fileURLToPath(new URL("board-desktop.png", evidence)),
    animations: "disabled",
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: fileURLToPath(new URL("board-mobile.png", evidence)),
    animations: "disabled",
  });
  const cancelledResponse = page.waitForResponse(
    (r) => r.request().method() === "POST" && r.url().endsWith("/cancel"),
  );
  await page.getByRole("button", { name: "取消协作计划", exact: true }).click();
  const cancelled = await (await cancelledResponse).json();
  expect(cancelled.status).toBe("cancelled");
  await page.reload();
  await page
    .getByRole("row")
    .filter({ hasText: title })
    .getByRole("button", { name: "查看", exact: true })
    .click();
  await dialog.getByRole("button", { name: "协作任务板", exact: true }).click();
  const readback = page.waitForResponse((r) =>
    r.url().endsWith(`/api/coordination-boards/${planned.board_id}`),
  );
  await page.getByRole("button", { name: "查看计划", exact: true }).click();
  expect(await (await readback).json()).toEqual(cancelled);
  expect(errors).toEqual([]);
  expect(forbidden).toEqual([]);
  writeFileSync(
    new URL("browser-live-readback.json", evidence),
    JSON.stringify(
      {
        title,
        planned,
        cancelled,
        privatePackage,
        model_calls: 0,
        page_errors: errors,
        forbidden_requests: forbidden,
      },
      null,
      2,
    ) + "\n",
    "utf8",
  );
});
