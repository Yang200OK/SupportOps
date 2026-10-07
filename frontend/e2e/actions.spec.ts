import { closeDetails } from "./ui-navigation";
import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

const account = JSON.parse(
  readFileSync(
    new URL("../../local/demo-accounts.json", import.meta.url),
    "utf8",
  ),
).accounts.find((a: { username: string }) => a.username === "support_a");
const inv = "cde57198-1b8b-40e4-95d9-123fd2133445";
const jobId = "a17b79c6-b775-4b7b-8cca-fd5a25100000";
const body = {
  job_id: jobId,
  status: "pending",
  proposal_sha256: "a".repeat(64),
  expires_at: "2099-01-01T00:00:00Z",
  approval: null,
  limitation: "页面协议验证；未运行真实模型或实验效果。",
  proposal: {
    effect: "清除实验缓存",
    choice: {
      action: "clear_cache",
      timeout_ms: null,
      reason: '<img src=x onerror="window.injected=true">',
      evidence_ids: ["live:test"],
    },
    retest: "一次真实投递",
    current_evidence: [
      { evidence_id: "live:test", text: '{"event":"target_observed"}' },
    ],
  },
  checkpoint: {},
};

async function login(page: Page) {
  await page.goto("/");
  await page.getByLabel("用户名", { exact: true }).fill(account.username);
  await page.getByLabel("密码", { exact: true }).fill(account.password);
  await page.getByRole("button", { name: "进入工作台" }).click();
}
async function create(page: Page) {
  await closeDetails(page);
  await page.getByRole("button", { name: "+ 新建工单" }).click();
  await page
    .getByLabel("问题标题", { exact: true })
    .fill(`动作页面协议 ${Date.now()}`);
  await page
    .getByLabel("问题描述", { exact: true })
    .fill("RelayDesk 1.1 缓存异常，批准有限实验验证。");
  await page.locator(".ticket-form select").first().selectOption("1.1");
  await page.getByRole("button", { name: "创建工单", exact: true }).click();
  await page
    .getByRole("dialog", { name: "工单详情", exact: true })
    .getByRole("button", { name: "批准动作", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "批准与实验动作", exact: true }),
  ).toBeVisible();
}
async function routes(page: Page) {
  await page.route("**/api/tickets/*/hypothesis-investigations?*", (r) =>
    r.fulfill({
      json: {
        items: [
          {
            investigation_id: inv,
            status: "completed",
            created_at: "2026-10-06T00:00:00Z",
          },
        ],
        total: 1,
      },
    }),
  );
  await page.route("**/api/tickets/*/actions", (r) =>
    r.fulfill({ json: { items: [] } }),
  );
}

test("REQ-1407: 具体批准、分步执行、复测和 SSE（页面协议）", async ({
  page,
}) => {
  await routes(page);
  let advances = 0;
  await page.route("**/action-proposals", (r) => r.fulfill({ json: body }));
  await page.route("**/actions/*/decision", async (r) => {
    expect(r.request().postDataJSON()).toEqual({
      decision: "approve",
      proposal_sha256: body.proposal_sha256,
    });
    await r.fulfill({
      json: {
        ...body,
        status: "approved",
        approval: {
          decision: "approve",
          user_id: "protocol-only",
          created_at: "2026-10-06",
        },
      },
    });
  });
  await page.route("**/actions/*/advance", (r) => {
    advances++;
    return r.fulfill({
      json: {
        ...body,
        status: advances === 1 ? "action_done" : "completed",
        checkpoint:
          advances === 1
            ? { action_receipt: { released: true } }
            : { retest_passed: true, retest_receipt: { observations: [] } },
      },
    });
  });
  await page.route("**/actions/*/events", (r) =>
    r.fulfill({
      contentType: "text/event-stream",
      body: 'id: 1\nevent: action_event\ndata: {"sequence":1,"event":"human_approve"}\n\n',
    }),
  );
  await login(page);
  await create(page);
  const panel = page.getByRole("region", { name: "批准与实验动作" });
  await panel.getByRole("button", { name: "生成待批准动作建议" }).click();
  await expect(
    panel.getByText(body.proposal.choice.reason, { exact: true }),
  ).toBeVisible();
  expect(await panel.locator("img").count()).toBe(0);
  expect(advances).toBe(0);
  await panel.getByRole("button", { name: "批准此动作与一次复测" }).click();
  await expect(panel.getByText("已批准，尚未执行 · approved")).toBeVisible();
  expect(advances).toBe(0);
  await panel.getByRole("button", { name: "执行下一步 / 恢复" }).click();
  await expect(
    panel.getByText("动作完成，等待复测 · action_done"),
  ).toBeVisible();
  await panel.getByRole("button", { name: "执行下一步 / 恢复" }).click();
  await expect(
    panel.getByText("真实投递复测：通过", { exact: false }),
  ).toBeVisible();
  await panel.getByRole("button", { name: "接收 / 重放事件" }).click();
  await expect(
    panel.locator("li").filter({ hasText: "human_approve" }),
  ).toBeVisible();
  await page.screenshot({
    path: "../docs/verification/phase-5-round-3/actions-protocol-desktop.png",
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await panel.scrollIntoViewIfNeeded();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "../docs/verification/phase-5-round-3/actions-protocol-mobile.png",
  });
});

test("REQ-1407: 换工单后丢弃迟到动作建议（页面协议）", async ({ page }) => {
  await routes(page);
  let reply!: () => Promise<void>;
  let started!: () => void;
  const dispatched = new Promise<void>((resolve) => {
    started = resolve;
  });
  await page.route("**/action-proposals", async (r) => {
    await new Promise<void>((resolve) => {
      reply = async () => {
        await r.fulfill({ json: body });
        resolve();
      };
      started();
    });
  });
  await login(page);
  await create(page);
  await page.getByRole("button", { name: "生成待批准动作建议" }).click();
  await dispatched;
  await create(page);
  await reply();
  await expect(
    page.getByRole("button", { name: "批准此动作与一次复测" }),
  ).toHaveCount(0);
  await expect(
    page.getByText(body.proposal.choice.reason, { exact: true }),
  ).toHaveCount(0);
});

test("REQ-UI-06: 隐藏后重开同一工单保留请求与结果，不重复建议（协议）", async ({
  page,
}) => {
  await routes(page);
  let release!: () => void;
  let calls = 0;
  await page.route("**/action-proposals", async (route) => {
    calls++;
    await new Promise<void>((resolve) => {
      release = resolve;
    });
    await route.fulfill({ json: body });
  });
  await login(page);
  await create(page);
  await page.getByRole("button", { name: "生成待批准动作建议" }).click();
  await expect.poll(() => calls).toBe(1);
  await closeDetails(page);
  await page.getByRole("button", { name: "查看", exact: true }).first().click();
  await page
    .getByRole("dialog", { name: "工单详情", exact: true })
    .getByRole("button", { name: "批准动作", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "生成待批准动作建议" }),
  ).toBeDisabled();
  release();
  await expect(page.locator(".action-result")).toContainText(
    body.proposal.choice.reason,
  );
  expect(calls).toBe(1);
});

test("REQ-1407: 重启后真实缓存复测与回执（零新增模型）", async ({ page }) => {
  const folder = new URL(
    "../../docs/verification/phase-5-round-3/",
    import.meta.url,
  );
  const report = JSON.parse(
    readFileSync(new URL("live-cache-attempt3.json", folder), "utf8"),
  );
  expect(report.status).toBe("completed");
  let modelPosts = 0;
  page.on("request", (r) => {
    if (
      r.method() === "POST" &&
      (r.url().endsWith("/hypothesis-investigations") ||
        r.url().endsWith("/action-proposals"))
    )
      modelPosts++;
  });
  await login(page);
  await expect(page.locator(".ticket-title").first()).toBeVisible();
  const row = page
    .locator(".table-scroll tr")
    .filter({ has: page.getByText(report.ticket.title, { exact: true }) });
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
    .getByRole("button", { name: "批准动作", exact: true })
    .click();
  const panel = page.getByRole("region", { name: "批准与实验动作" });
  await panel.getByText("动作历史（最近 30 条）", { exact: true }).click();
  // 历史动作已经完成；本轮只读核对，不能再次执行复测。
  const response = page.waitForResponse(
    (r) =>
      r.request().method() === "GET" &&
      r.url().endsWith(`/api/actions/${report.job_id}`),
  );
  await panel
    .getByRole("button", {
      name: new RegExp(`^查看动作 ${report.job_id.slice(0, 8)}`),
    })
    .click();
  const body = await (await response).json();
  expect(body.status).toBe("completed");
  expect(body).toEqual(report.after_restart);
  expect(body.checkpoint.retest_passed).toBe(true);
  expect(body.checkpoint.action_receipt).toEqual(
    report.before_restart.checkpoint.action_receipt,
  );
  await expect(
    panel.getByText("真实投递复测：通过", { exact: false }),
  ).toBeVisible();
  await panel.getByRole("button", { name: "接收 / 重放事件" }).click();
  await expect(
    panel.locator("li").filter({ hasText: "retest_completed" }),
  ).toBeVisible();
  expect(modelPosts).toBe(0);
  await panel
    .locator(".action-result")
    .evaluate((e) => e.scrollIntoView({ block: "start" }));
  await page.screenshot({
    path: "../docs/verification/phase-6-round-2/old-actions-desktop.png",
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await panel
    .locator(".action-result")
    .evaluate((e) => e.scrollIntoView({ block: "start" }));
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "../docs/verification/phase-6-round-2/old-actions-mobile.png",
  });
});
