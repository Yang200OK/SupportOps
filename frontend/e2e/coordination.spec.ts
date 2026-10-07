import { expect, test } from "@playwright/test";

// 页面协议替身只核对交互，真实持久化由独立集成用例验证。
test("REQ-1806: 协作入口、私有包和移动布局（页面协议）", async ({ page }) => {
  test.setTimeout(30000);
  const ticket = {
    ticket_id: "ticket-one",
    title: "协作任务板协议",
    description: "RD_TIMEOUT",
    product: "relaydesk",
    product_version: "1.1",
    environment: "local_lab",
    source_type: "synthetic_case",
    missing_fields: [],
    intake_status: "ready",
    created_at: "2026-10-07T00:00:00Z",
  };
  const tasks = [
    {
      task_id: "documents",
      role: "document_agent",
      depends_on: [],
      status: "ready",
      objective: "核对同版本规则",
      tools: ["search_knowledge"],
      budget: { tool_calls: 3, model_calls: 3, context_chars: 5334 },
    },
    {
      task_id: "runtime",
      role: "runtime_agent",
      depends_on: [],
      status: "ready",
      objective: "核对现场观测",
      tools: ["read_startup_diagnostic"],
      budget: { tool_calls: 3, model_calls: 3, context_chars: 5333 },
    },
    {
      task_id: "synthesis",
      role: "coordinator",
      depends_on: ["documents", "runtime"],
      status: "blocked",
      objective: "核对证据缺口",
      tools: [],
      budget: { tool_calls: 0, model_calls: 2, context_chars: 5333 },
    },
  ];
  let board = {
    board_id: "board-one",
    ticket_id: ticket.ticket_id,
    status: "planned",
    revision: 1,
    tasks,
    execution_started: false,
    usage: { model_calls: 0, tool_calls: 0 },
    budget: {
      tool_calls: 6,
      model_calls: 8,
      context_chars: 16000,
      time_budget_ms: 240000,
    },
    events: [],
  };
  let created = false;
  let posted: Record<string, unknown> = {};
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    let body: unknown;
    if (url.pathname === "/api/auth/login")
      body = { access_token: "protocol", principal: {} };
    else if (url.pathname === "/api/auth/me")
      body = { username: "protocol", organization_name: "页面协议组织" };
    else if (url.pathname === "/api/tickets")
      body = { items: [ticket], total: 1, offset: 0, limit: 10 };
    else if (url.pathname === "/api/tickets/ticket-one") body = ticket;
    else if (url.pathname === "/api/retrieval/indexes")
      body = { items: [{ index_id: "index-one", entry_count: 502 }], total: 1 };
    else if (url.pathname === "/api/investigation-lab-runs")
      body = {
        items: [
          { lab_run_id: "run-one", product_version: "1.1", mode: "startup" },
          { lab_run_id: "run-two", product_version: "1.1", mode: "online" },
        ],
        total: 1,
      };
    else if (url.pathname.endsWith("/coordination-boards")) {
      if (route.request().method() === "POST") {
        posted = route.request().postDataJSON();
        created = true;
        body = board;
      } else
        body = {
          items: created ? [board] : [],
          total: created ? 1 : 0,
          offset: 0,
          limit: 10,
        };
    } else if (url.pathname.endsWith("/tasks/documents/package"))
      body = {
        package: { role: "document_agent", tools: ["search_knowledge"] },
        execution_allowed: false,
      };
    else if (url.pathname.endsWith("/cancel")) {
      board = {
        ...board,
        status: "cancelled",
        revision: 2,
        tasks: tasks.map((t) => ({ ...t, status: "cancelled" })),
      };
      body = board;
    } else if (url.pathname === "/api/coordination-boards/board-one")
      body = board;
    else
      return route.fulfill({
        status: 404,
        json: { error: { code: "PROTOCOL_NOT_FOUND", message: url.pathname } },
      });
    await route.fulfill({ json: body });
  });
  await page.route("**/health/ready", (r) =>
    r.fulfill({ json: { database: "ready" } }),
  );
  await page.goto("/");
  await page.getByLabel("用户名", { exact: true }).fill("protocol");
  await page.getByLabel("密码", { exact: true }).fill("protocol-password");
  await page.getByRole("button", { name: "进入工作台" }).click();
  await page.getByRole("button", { name: "查看", exact: true }).click();
  await page.getByRole("button", { name: "协作任务板", exact: true }).click();
  await page.getByLabel("协作本次实验").selectOption("run-one");
  await page.getByRole("button", { name: "创建协作计划", exact: true }).click();
  await expect(page.locator(".coordination-panel")).toContainText("等待依赖");
  await expect(page.locator(".coordination-panel")).toContainText("尚未执行");
  expect(posted).toMatchObject({
    index_id: "index-one",
    lab_run_id: "run-one",
  });
  expect(posted).not.toHaveProperty("tasks");
  await page
    .getByRole("button", { name: "查看任务包", exact: true })
    .first()
    .click();
  const dialog = page.getByRole("dialog", { name: "私有任务包", exact: true });
  await expect(dialog).toContainText("search_knowledge");
  await expect(dialog).not.toContainText("read_startup_diagnostic");
  await page.keyboard.press("Escape");
  await expect(dialog).not.toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.getByRole("button", { name: "取消协作计划", exact: true }).click();
  await expect(page.locator(".coordination-panel")).toContainText("已取消");
  await page.getByLabel("协作本次实验").selectOption("run-two");
  await expect(
    page.getByRole("button", { name: "取消协作计划", exact: true }),
  ).toHaveCount(0);
});
