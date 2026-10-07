import { expect, test } from "@playwright/test";

// 协议替身验证两波交互、取消与原文，不声称真实模型质量。
test("REQ-1906: 协作执行两波、冲突、原文与取消（页面协议）", async ({
  page,
}) => {
  const ticket = {
    ticket_id: "ticket-execution",
    title: "协作执行协议",
    description: "RD_CONFIG_INVALID",
    product: "relaydesk",
    product_version: "1.1",
    environment: "local_lab",
    source_type: "synthetic_case",
    missing_fields: [],
    intake_status: "complete",
    created_at: "2026-10-07T00:00:00Z",
  };
  const budget = {
    tool_calls: 6,
    model_calls: 8,
    context_chars: 16000,
    time_budget_ms: 240000,
  };
  const board = {
    board_id: "board-execution",
    status: "planned",
    revision: 1,
    created_at: ticket.created_at,
    budget,
    tasks: [],
    events: [],
  };
  let waves = 0;
  let run = {
    execution_id: "execution-one",
    status: "pending",
    revision: 1,
    error: null,
    usage: { model_calls: 0, tool_calls: 0, context_chars: 0 },
    model_accounting: {
      known_calls: 0,
      unknown_calls: 0,
      input_tokens: 0,
      output_tokens: 0,
    },
    tasks: {
      documents: { status: "pending", phase: "plan", report: null },
      runtime: { status: "pending", phase: "plan", report: null },
      synthesis: { status: "pending", phase: "plan", report: null },
    },
    result: null as unknown,
    events: [],
  };
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let value: unknown;
    if (path === "/api/auth/login")
      value = { access_token: "protocol", principal: {} };
    else if (path === "/api/auth/me")
      value = { username: "protocol", organization_name: "协议组织" };
    else if (path === "/api/tickets")
      value = { items: [ticket], total: 1, offset: 0, limit: 10 };
    else if (path === "/api/tickets/ticket-execution") value = ticket;
    else if (path === "/api/retrieval/indexes")
      value = { items: [{ index_id: "index-one", entry_count: 502 }] };
    else if (path === "/api/investigation-lab-runs") value = { items: [] };
    else if (path.endsWith("/coordination-boards"))
      value = { items: [board], total: 1, offset: 0, limit: 10 };
    else if (path === "/api/coordination-boards/board-execution") value = board;
    else if (path.endsWith("/executions")) {
      if (route.request().method() === "POST") {
        expect(Object.keys(route.request().postDataJSON())).toEqual([
          "request_id",
        ]);
        value = run;
      } else value = { items: [run] };
    } else if (path.endsWith("/advance")) {
      waves++;
      run = {
        ...run,
        status: waves === 1 ? "pending" : "completed",
        usage: {
          model_calls: waves === 1 ? 4 : 6,
          tool_calls: 2,
          context_chars: 3200,
        },
        model_accounting: {
          known_calls: waves === 1 ? 4 : 6,
          unknown_calls: 0,
          input_tokens: 20,
          output_tokens: 10,
        },
        tasks: {
          documents: { status: "completed", phase: "done", report: null },
          runtime: { status: "completed", phase: "done", report: null },
          synthesis: {
            status: waves === 1 ? "pending" : "completed",
            phase: waves === 1 ? "plan" : "done",
            report: null,
          },
        },
        result:
          waves === 1
            ? null
            : {
                claims: [
                  {
                    claim_id: "C1",
                    text: "配置诊断仍待验证",
                    support: { verdict: "supported" },
                    citations: [{ evidence_id: "e1", quote: "原句" }],
                  },
                ],
                conflicts: [{ status: "unresolved" }],
                missing_information: ["人工核对"],
              },
      };
      value = run;
    } else if (path.endsWith("/cancel")) {
      run = { ...run, status: "cancelled" };
      value = run;
    } else if (path.endsWith("/evidence/e1"))
      value = { evidence: { text: "原句", text_verified: true } };
    else if (path === "/api/coordination-executions/execution-one") value = run;
    else
      return route.fulfill({
        status: 404,
        json: { error: { code: "PROTOCOL_NOT_FOUND", message: path } },
      });
    await route.fulfill({ json: value });
  });
  await page.route("**/health/ready", (r) =>
    r.fulfill({ json: { database: "ready" } }),
  );
  await page.goto("/");
  await page.getByLabel("用户名", { exact: true }).fill("protocol");
  await page.getByLabel("密码", { exact: true }).fill("protocol-pass");
  await page.getByRole("button", { name: "进入工作台" }).click();
  await page.getByRole("button", { name: "查看", exact: true }).click();
  await page.getByRole("button", { name: "协作任务板", exact: true }).click();
  await page.getByRole("button", { name: "查看计划", exact: true }).click();
  await page.getByRole("button", { name: "创建协作执行", exact: true }).click();
  await expect(page.locator(".execution-panel")).toContainText(
    "执行状态：待推进",
  );
  await page.getByRole("button", { name: "推进就绪任务", exact: true }).click();
  await expect(page.locator(".execution-panel")).toContainText("实际 4 次模型");
  await page.getByRole("button", { name: "推进就绪任务", exact: true }).click();
  await expect(page.locator(".execution-panel")).toContainText(
    "执行状态：已完成",
  );
  await page.getByRole("button", { name: "查看归并报告", exact: true }).click();
  await page.getByRole("button", { name: "查看协作原文", exact: true }).click();
  await expect(
    page.getByRole("dialog", { name: "协作证据原文" }),
  ).toContainText("原句");
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "查看归并报告", exact: true }).click();
  await page.getByRole("button", { name: "查看冲突", exact: true }).click();
  await expect(
    page.getByRole("dialog", { name: "未决证据冲突" }),
  ).toContainText("unresolved");
  await page.keyboard.press("Escape");
  run = { ...run, status: "pending" };
  await page.getByRole("button", { name: "刷新执行", exact: true }).click();
  await page.getByRole("button", { name: "取消协作执行", exact: true }).click();
  await expect(page.locator(".execution-panel")).toContainText(
    "执行状态：已取消",
  );
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});
