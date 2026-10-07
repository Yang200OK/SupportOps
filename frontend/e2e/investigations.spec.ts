import { closeDetails } from "./ui-navigation";
import { expect, test, type Page } from "@playwright/test";
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const accounts = JSON.parse(
  readFileSync(
    new URL("../../local/demo-accounts.json", import.meta.url),
    "utf8",
  ),
).accounts as { username: string; password: string }[];
const folder = new URL(
  "../../docs/verification/phase-5-round-1/",
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
    .fill(`调查基线构造案例 ${Date.now()}`);
  await page
    .getByLabel("问题描述", { exact: true })
    .fill(
      "RelayDesk 1.1 报 RD_CONFIG_INVALID，请整理文档中的配置要求；没有当前现场日志，不确认根因。",
    );
  await page.locator(".ticket-form select").first().selectOption("1.1");
  await page.getByRole("button", { name: "创建工单", exact: true }).click();
  await page
    .getByRole("dialog", { name: "工单详情", exact: true })
    .getByRole("button", { name: "只读调查", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "只读调查基线", exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel("调查知识快照")).not.toHaveValue("");
}

test("REQ-1207: 真实模型 / MCP 调查、历史读回与原文", async ({ page }) => {
  test.setTimeout(240000);
  await openTicket(page);
  const wait = page.waitForResponse(
    (r) =>
      r.url().endsWith("/investigations") && r.request().method() === "POST",
    { timeout: 200000 },
  );
  await page.getByRole("button", { name: "开始只读调查", exact: true }).click();
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
  await expect(page.locator(".investigation-result")).toContainText(
    "baseline_complete",
  );
  await page.getByRole("button", { name: "回查调查引文" }).first().click();
  await expect(page.locator(".investigation-reference")).toContainText(
    "text_verified",
  );
  await page.screenshot({
    path: new URL(
      "regression-investigations-investigation-desktop.png",
      new URL(
        "../../docs/verification/ui-refinement-before-phase-7/",
        import.meta.url,
      ),
    ).pathname.replace(/^\//, ""),
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: new URL(
      "regression-investigations-investigation-mobile.png",
      new URL(
        "../../docs/verification/ui-refinement-before-phase-7/",
        import.meta.url,
      ),
    ).pathname.replace(/^\//, ""),
  });
  await page.getByRole("button", { name: "读取历史调查" }).first().click();
  await expect(page.locator(".investigation-result")).toContainText(
    body.investigation_id,
  );
});

test("REQ-1207: 范围变化清空并丢弃迟到响应（页面协议）", async ({ page }) => {
  await openTicket(page);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let arrival!: () => void;
  const entered = new Promise<void>((resolve) => {
    arrival = resolve;
  });
  await page.route("**/api/tickets/*/investigations", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    arrival();
    await gate;
    await route.fulfill({
      status: 201,
      json: {
        investigation_id: "late-protocol-only",
        status: "stopped",
        stop_reason: "tool_budget",
        report: null,
        events: [],
        evidence: [],
        usage: { known_model_calls: 0, unknown_model_calls: 0 },
        limitation: "页面协议",
      },
    });
  });
  await page.getByRole("button", { name: "开始只读调查", exact: true }).click();
  await entered;
  await page.getByLabel("调查历史实验").selectOption({ index: 1 });
  release();
  await expect(
    page.getByRole("button", { name: "开始只读调查", exact: true }),
  ).toBeEnabled();
  await expect(page.locator(".investigation-result")).toHaveCount(0);
  await page.getByRole("button", { name: "退出登录", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "登录支持工作台" }),
  ).toBeVisible();
});

test("REQ-1207: 未支持项、文本转义、原文与手机布局（页面协议）", async ({
  page,
}) => {
  await openTicket(page);
  const value = {
    investigation_id: "browser-protocol-only",
    status: "completed",
    stop_reason: "baseline_complete",
    duration_ms: 1,
    limitation: "页面协议演示；模型语义判断尚未人工复核。",
    events: [{ sequence: 1, event: "tool_finished", tool: "search_knowledge" }],
    usage: {
      known_model_calls: 0,
      unknown_model_calls: 0,
      input_tokens: 0,
      output_tokens: 0,
    },
    input_snapshot: { ticket: { product_version: "1.1" } },
    evidence: [
      {
        evidence_id: "E1",
        reference_url: "/api/chunk-sets/a/chunks/b/citation",
      },
    ],
    report: {
      claims: [
        {
          claim_id: "C1",
          kind: "fact",
          text: '<img src=x onerror="window.injected=true">',
          support: {
            verdict: "unsupported",
            reason: "引文无关。",
            human_reviewed: false,
          },
          citations: [{ evidence_id: "E1", quote: "页面协议原句" }],
        },
      ],
      missing_information: ["需要当前日志。"],
    },
  };
  await page.route("**/api/tickets/*/investigations", (route) =>
    route.request().method() === "POST"
      ? route.fulfill({ status: 201, json: value })
      : route.continue(),
  );
  await page.route("**/api/chunk-sets/a/chunks/b/citation", (route) =>
    route.fulfill({
      json: { text_verified: true, chunk: { text: "页面协议原句" } },
    }),
  );
  await page.getByRole("button", { name: "开始只读调查", exact: true }).click();
  await expect(page.locator(".investigation-claim")).toContainText(
    "不应作为已证实事实",
  );
  await expect(page.locator(".investigation-claim")).toContainText(
    "<img src=x",
  );
  await expect(page.locator(".investigation-claim img")).toHaveCount(0);
  await page.getByRole("button", { name: "回查调查引文" }).click();
  await expect(page.locator(".investigation-reference")).toContainText(
    "页面协议原句",
  );
  await page.screenshot({
    path: fileURLToPath(
      new URL(
        "regression-investigations-protocol-desktop.png",
        new URL(
          "../../docs/verification/ui-refinement-before-phase-7/",
          import.meta.url,
        ),
      ),
    ),
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.locator(".investigation-panel").scrollIntoViewIfNeeded();
  await page.screenshot({
    path: fileURLToPath(
      new URL(
        "regression-investigations-protocol-mobile.png",
        new URL(
          "../../docs/verification/ui-refinement-before-phase-7/",
          import.meta.url,
        ),
      ),
    ),
  });
});
