import { closeDetails } from "./ui-navigation";
import { expect, test, type Page } from "@playwright/test";
import { readFileSync, readdirSync, writeFileSync } from "node:fs";

const accounts = JSON.parse(
  readFileSync(
    new URL("../../local/demo-accounts.json", import.meta.url),
    "utf8",
  ),
).accounts as { username: string; password: string }[];
const folder = new URL(
  "../../docs/verification/phase-5-round-2/",
  import.meta.url,
);

async function openTicket(page: Page) {
  const account = accounts.find((a) => a.username === "support_a")!;
  await page.goto("/");
  await page.getByLabel("用户名", { exact: true }).fill(account.username);
  await page.getByLabel("密码", { exact: true }).fill(account.password);
  await page.getByRole("button", { name: "进入工作台" }).click();
  await page.getByRole("button", { name: "+ 新建工单" }).click();
  await page
    .getByLabel("问题标题", { exact: true })
    .fill(`本次实验假设调查 ${Date.now()}`);
  await page
    .getByLabel("问题描述", { exact: true })
    .fill(
      "RelayDesk 1.1 投递出现 RD_TIMEOUT，请根据绑定的本次实验观测调查原因候选，证据不足时保持未决。",
    );
  await page.locator(".ticket-form select").first().selectOption("1.1");
  await page.getByRole("button", { name: "创建工单", exact: true }).click();
  await page
    .getByRole("dialog", { name: "工单详情", exact: true })
    .getByRole("button", { name: "假设调查", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "假设调查", exact: true }),
  ).toBeVisible();
}

test("REQ-1307: 假设、冲突、停止与迟到范围（页面协议）", async ({ page }) => {
  test.setTimeout(45000);
  await page.route("**/api/investigation-lab-runs?*", (route) =>
    route.fulfill({
      json: {
        items: [
          {
            lab_run_id: "protocol-a",
            product_version: "1.1",
            mode: "online",
            expires_at: "2099-01-01T00:00:00Z",
          },
          {
            lab_run_id: "protocol-b",
            product_version: "1.1",
            mode: "startup",
            expires_at: "2099-01-01T00:00:00Z",
          },
        ],
        total: 2,
        offset: 0,
        limit: 30,
      },
    }),
  );
  await openTicket(page);
  await page.getByLabel("本次实验运行").selectOption("protocol-a");
  const value = {
    investigation_id: "protocol-only",
    status: "stopped",
    stop_reason: "tool_budget",
    limitation: "页面协议；未执行模型",
    duration_ms: 1,
    usage: {
      known_model_calls: 0,
      unknown_model_calls: 0,
      input_tokens: 0,
      output_tokens: 0,
    },
    hypotheses: [
      {
        hypothesis_id: "H1",
        cause: '<img src=x onerror="window.injected=true">',
        basis: "检查超时",
        status: "unresolved",
        reason: "观测冲突，保持未决",
        support_signal: "下游慢",
        refute_signal: "链路尚未进入下游",
        support_citations: [],
        refute_citations: [],
        missing_information: ["补充观测"],
        semantic_reviewed: false,
      },
    ],
    steps: [
      {
        step_id: "S1",
        tool: "read_current_observations",
        status: "completed",
        reason: "核对本次请求",
      },
      {
        step_id: "S2",
        tool: "read_runtime_state",
        status: "pending",
        reason: "尚未执行",
      },
    ],
    events: [],
    evidence: [],
    plan_history: [],
    report: null,
  };
  await page.route("**/api/tickets/*/hypothesis-investigations", (route) =>
    route.request().method() === "POST"
      ? route.fulfill({ status: 201, json: value })
      : route.continue(),
  );
  await page.getByRole("button", { name: "开始假设调查", exact: true }).click();
  await expect(page.locator(".hypothesis-result")).toContainText("tool_budget");
  await expect(page.locator(".hypothesis-card")).toContainText("观测冲突");
  expect(await page.evaluate(() => "injected" in window)).toBe(false);
  await expect(page.locator(".hypothesis-steps")).toContainText("待检查");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.locator(".hypothesis-result").scrollIntoViewIfNeeded();
  await page.screenshot({
    path: new URL(
      "regression-hypotheses-hypothesis-protocol-mobile.png",
      new URL(
        "../../docs/verification/ui-refinement-before-phase-7/",
        import.meta.url,
      ),
    ).pathname.replace(/^\//, ""),
  });
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let arrival!: () => void;
  const entered = new Promise<void>((resolve) => {
    arrival = resolve;
  });
  await page.unroute("**/api/tickets/*/hypothesis-investigations");
  await page.route(
    "**/api/tickets/*/hypothesis-investigations",
    async (route) => {
      if (route.request().method() !== "POST") return route.continue();
      arrival();
      await gate;
      await route.fulfill({ status: 201, json: value });
    },
  );
  await page.getByRole("button", { name: "开始假设调查", exact: true }).click();
  await entered;
  await page.getByLabel("本次实验运行").selectOption("protocol-b");
  release();
  await expect(
    page.getByRole("button", { name: "开始假设调查", exact: true }),
  ).toBeEnabled();
  await expect(page.locator(".hypothesis-result")).toHaveCount(0);
});

test("REQ-1308: 真实模型、当前观测、假设轨迹与原文", async ({ page }) => {
  test.setTimeout(300000);
  await openTicket(page);
  const prepared = JSON.parse(
    readFileSync(
      new URL("../../local/phase5-round2-browser-run.json", import.meta.url),
      "utf8",
    ),
  );
  await page.getByLabel("本次实验运行").selectOption(prepared.lab_run_id);
  const wait = page.waitForResponse(
    (r) =>
      r.url().endsWith("/hypothesis-investigations") &&
      r.request().method() === "POST",
    { timeout: 260000 },
  );
  await page.getByRole("button", { name: "开始假设调查", exact: true }).click();
  const response = await wait;
  expect(response.status()).toBe(201);
  const body = await response.json();
  writeFileSync(
    new URL(
      `browser-live-${body.investigation_id}.json`,
      new URL(
        "../../docs/verification/ui-refinement-before-phase-7/",
        import.meta.url,
      ),
    ),
    JSON.stringify(body, null, 2) + "\n",
  );
  expect(body.status, body.stop_reason).toBe("completed");
  expect(body.has_current_evidence).toBe(true);
  expect(body.plan_history.length).toBeGreaterThan(1);
  await expect(page.locator(".hypothesis-card")).toHaveCount(
    body.hypotheses.length,
  );
  await page
    .getByRole("button", { name: "回查调查原文", exact: true })
    .last()
    .click();
  await expect(page.locator(".hypothesis-reference")).toContainText(
    "text_verified",
  );
  await page.locator(".hypothesis-result").scrollIntoViewIfNeeded();
  await page.screenshot({
    path: new URL(
      "regression-hypotheses-hypothesis-desktop.png",
      new URL(
        "../../docs/verification/ui-refinement-before-phase-7/",
        import.meta.url,
      ),
    ).pathname.replace(/^\//, ""),
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator(".hypothesis-result").scrollIntoViewIfNeeded();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: new URL(
      "regression-hypotheses-hypothesis-mobile.png",
      new URL(
        "../../docs/verification/ui-refinement-before-phase-7/",
        import.meta.url,
      ),
    ).pathname.replace(/^\//, ""),
  });
  await page
    .getByRole("button", { name: "读取历史假设调查", exact: true })
    .first()
    .click();
  await expect(page.locator(".hypothesis-result")).toContainText(
    body.investigation_id,
  );
});

test("REQ-1307: 重启后只读历史与未决展示（零模型）", async ({ page }) => {
  const bodies = readdirSync(folder)
    .filter(
      (name) => name.startsWith("browser-live-") && name.endsWith(".json"),
    )
    .map((name) => JSON.parse(readFileSync(new URL(name, folder), "utf8")))
    .filter((body) => body.status === "completed")
    .sort(
      (a, b) =>
        Date.parse(b.input_snapshot.ticket.created_at) -
        Date.parse(a.input_snapshot.ticket.created_at),
    );
  expect(bodies.length).toBeGreaterThan(0);
  const body = bodies[0];
  let starts = 0;
  page.on("request", (r) => {
    if (r.method() === "POST" && r.url().endsWith("/hypothesis-investigations"))
      starts++;
  });
  const account = accounts.find((a) => a.username === "support_a")!;
  await page.goto("/");
  await page.getByLabel("用户名", { exact: true }).fill(account.username);
  await page.getByLabel("密码", { exact: true }).fill(account.password);
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(page.locator(".ticket-title").first()).toBeVisible();
  const row = page.locator(".table-scroll tr").filter({
    has: page.getByText(body.input_snapshot.ticket.title, { exact: true }),
  });
  for (let i = 0; i < 40 && !(await row.count()); i++) {
    // 新轮次增加工单后需要翻页；等真实列表返回，避免对旧页连续判断。
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
    page.getByRole("heading", { name: "假设调查", exact: true }),
  ).toBeVisible();
  const history = page
    .locator(".hypothesis-history")
    .filter({ hasText: body.investigation_id.slice(0, 12) });
  const response = page.waitForResponse(
    (r) =>
      r.request().method() === "GET" &&
      r.url().endsWith("/api/investigations/" + body.investigation_id),
  );
  await history
    .getByRole("button", { name: "读取历史假设调查", exact: true })
    .click();
  expect(await (await response).json()).toEqual(body);
  await expect(page.locator(".hypothesis-result")).toContainText(
    body.investigation_id,
  );
  if (
    body.hypotheses.some(
      (h: { status: string; proposed_status?: string }) =>
        h.proposed_status && h.proposed_status !== h.status,
    )
  ) {
    await expect(
      page.getByText("模型原建议（未通过语义核对）：", { exact: true }).first(),
    ).toBeVisible();
  }
  await page
    .getByRole("button", { name: "回查假设依据", exact: true })
    .first()
    .click();
  await expect(page.locator(".hypothesis-reference")).toContainText(
    "text_verified",
  );
  await page
    .locator(".hypothesis-panel")
    .evaluate((e) => e.scrollIntoView({ block: "start" }));
  await page.screenshot({
    path: new URL(
      "regression-hypotheses-hypothesis-top-desktop.png",
      new URL(
        "../../docs/verification/ui-refinement-before-phase-7/",
        import.meta.url,
      ),
    ).pathname.replace(/^\//, ""),
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page
    .locator(".hypothesis-result")
    .evaluate((e) => e.scrollIntoView({ block: "start" }));
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: new URL(
      "regression-hypotheses-hypothesis-top-mobile.png",
      new URL(
        "../../docs/verification/ui-refinement-before-phase-7/",
        import.meta.url,
      ),
    ).pathname.replace(/^\//, ""),
  });
  expect(starts).toBe(0);
  writeFileSync(
    new URL(
      "browser-restart-readback.json",
      new URL(
        "../../docs/verification/ui-refinement-before-phase-7/",
        import.meta.url,
      ),
    ),
    JSON.stringify(
      {
        investigation_id: body.investigation_id,
        response_unchanged: true,
        reference_checked: true,
        model_post_requests: starts,
        mobile_no_overflow: true,
        transport: "real_edge_after_api_restart",
      },
      null,
      2,
    ) + "\n",
  );
});
