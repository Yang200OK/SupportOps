import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

// 新评测复用既有界面读历史，不点击执行按钮或派发模型。
const root = new URL("../../", import.meta.url);
const read = (path: string) =>
  JSON.parse(readFileSync(new URL(path, root), "utf8"));

for (const variant of ["single", "memory", "multi", "multi-failure"]) {
  test(`REQ-2105: 最终 ${variant} 记录和原文只读展示`, async ({ page }) => {
    const report = read("docs/verification/phase-8-round-1/agents.json");
    const manifest = read("data/evaluations/final-v1/manifest.json");
    const failed = variant === "multi-failure";
    const chosen = failed ? "multi" : variant;
    const row = report.attempts.find(
      (r: { variant: string; status: string; response: unknown }) =>
        r.variant === chosen &&
        (failed ? r.status !== "completed" : true) &&
        r.response,
    );
    expect(row).toBeTruthy();
    const task = manifest.tasks.agents.find(
      (t: { task_id: string }) => t.task_id === row.task_id,
    );
    const account = read("local/demo-accounts.json").accounts.find(
      (a: { username: string }) => a.username === "support_a",
    );
    const posts: string[] = [];
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("request", (r) => {
      if (r.method() === "POST" && !r.url().endsWith("/login"))
        posts.push(r.url());
    });
    await page.goto("/");
    await page.getByLabel("用户名", { exact: true }).fill(account.username);
    await page.getByLabel("密码", { exact: true }).fill(account.password);
    await page.getByRole("button", { name: "进入工作台" }).click();
    const ticket = page.locator(".table-scroll tbody tr").filter({
      hasText: task.ticket.ticket_id.slice(0, 8),
    });
    await expect(page.locator(".ticket-title").first()).toBeVisible();
    for (let i = 0; i < 40 && !(await ticket.count()); i++) {
      const response = page.waitForResponse(
        (r) =>
          r.request().method() === "GET" &&
          new URL(r.url()).pathname === "/api/tickets",
      );
      await page
        .getByRole("button", { name: "下一页", exact: true })
        .first()
        .click();
      await response;
      await expect(page.locator(".loading")).toHaveCount(0);
    }
    await expect(ticket).toHaveCount(1);
    await ticket.getByRole("button", { name: "查看", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "工单详情", exact: true });
    if (chosen === "multi") {
      await dialog
        .getByRole("button", { name: "协作任务板", exact: true })
        .click();
      await page
        .locator(".coordination-history")
        .filter({ hasText: row.board.board_id.slice(0, 8) })
        .getByRole("button", { name: "查看计划", exact: true })
        .click();
      await page
        .getByRole("button", { name: "读取执行历史", exact: true })
        .click();
      const history = page.locator(".execution-task").filter({
        hasText: row.response.execution_id.slice(0, 8),
      });
      await expect(history).toBeVisible();
      const response = page.waitForResponse(
        (r) =>
          r.request().method() === "GET" &&
          new URL(r.url()).pathname === row.record_path,
      );
      await history.getByRole("button").click();
      expect(await (await response).json()).toEqual(row.response);
      await expect(page.locator(".execution-summary")).toContainText(
        `实际 ${row.response.usage.model_calls} 次模型`,
      );
      if (row.status !== "completed") {
        await expect(
          page.locator(".execution-summary .error").first(),
        ).toBeVisible();
      } else {
        await page
          .getByRole("button", { name: "查看归并报告", exact: true })
          .click();
        const result = page.getByRole("dialog", {
          name: "协作归并报告",
          exact: true,
        });
        await expect(result).toContainText("尚未经人工复核");
        await result
          .getByRole("button", { name: "查看协作原文", exact: true })
          .first()
          .click();
        await expect(
          page.getByRole("dialog", { name: "协作证据原文", exact: true }),
        ).toContainText("evidence");
      }
    } else {
      await dialog
        .getByRole("button", { name: "假设调查", exact: true })
        .click();
      const response = page.waitForResponse(
        (r) =>
          r.request().method() === "GET" &&
          new URL(r.url()).pathname === row.record_path,
      );
      await page
        .locator(".hypothesis-history")
        .filter({ hasText: row.response.investigation_id.slice(0, 12) })
        .getByRole("button", { name: "读取历史假设调查", exact: true })
        .click();
      expect(await (await response).json()).toEqual(row.response);
      await expect(page.locator(".hypothesis-result")).toContainText(
        row.response.investigation_id,
      );
      if (row.status === "completed") {
        await page
          .getByRole("button", { name: "回查调查原文", exact: true })
          .first()
          .click();
        await expect(page.locator(".hypothesis-reference")).toContainText(
          "text_verified",
        );
      } else {
        await expect(page.locator(".hypothesis-result")).toContainText(
          row.response.stop_reason,
        );
      }
    }
    await page.setViewportSize({ width: 390, height: 844 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      animations: "disabled",
      path: fileURLToPath(
        new URL(
          `docs/verification/phase-8-round-1/${variant}-mobile.png`,
          root,
        ),
      ),
    });
    expect(posts).toEqual([]);
    expect(errors).toEqual([]);
  });
}
