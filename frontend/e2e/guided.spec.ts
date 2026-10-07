import { expect, test } from "@playwright/test";
import { readFileSync, appendFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const accounts = JSON.parse(
  readFileSync(
    new URL("../../local/demo-accounts.json", import.meta.url),
    "utf8",
  ),
).accounts as { username: string; password: string }[];

async function login(page: import("@playwright/test").Page) {
  const account = accounts.find((a) => a.username === "support_a")!;
  await page.goto("/");
  await page.getByLabel("用户名", { exact: true }).fill(account.username);
  await page.getByLabel("密码", { exact: true }).fill(account.password);
  await page.getByRole("button", { name: "进入工作台" }).click();
  await page.getByRole("button", { name: /知识与资料/ }).click();
  await page.getByRole("button", { name: "证据检索", exact: true }).click();
}

test("REQ-1008: 分步问答入口与未确认版本", async ({ page }) => {
  await login(page);
  const button = page.getByRole("button", {
    name: "分步证据问答",
    exact: true,
  });
  await expect(button).toBeVisible();
  await page
    .getByRole("combobox", { name: "产品版本", exact: true })
    .selectOption("unknown");
  await page.getByLabel("检索问题").fill("升级后请求超时");
  const pending = page.waitForResponse((r) =>
    r.url().endsWith("/guided-answer"),
  );
  await button.click();
  const response = await pending;
  expect(response.status()).toBe(200);
  const body = await response.json();
  expect(body.status).toBe("needs_clarification");
  expect(body.usage.known_model_calls).toBe(0);
  await expect(page.locator(".guided-panel")).toContainText(
    "请选择当前实际产品版本",
  );
  await expect(
    page.getByRole("button", { name: "生成证据回答", exact: true }),
  ).toBeDisabled();
});

// 页面替身只验证交互协议；不作为真实模型冲突或回答准确率。
const protocol = {
  original_query: "RD_TIMEOUT",
  rewritten_query: "RD_TIMEOUT 文档排查",
  clarification: null,
  status: "needs_clarification",
  stop_reason: "query_clarification",
  questions: ["请提供具体错误现象。"],
  conflicts: [],
  trace: [{ stage: "stop", reason: "query_clarification" }],
  usage: {
    known_model_calls: 1,
    unknown_usage_calls: 0,
    input_tokens: 10,
    output_tokens: 7,
    cost_cny: null,
  },
  answer: null,
  retrieval: null,
};

test("REQ-1008: 补充后重跑与重复查询停止提示", async ({ page }) => {
  await login(page);
  await page.getByLabel("检索问题").fill("RD_TIMEOUT");
  const received: Record<string, unknown>[] = [];
  await page.route(
    "**/api/retrieval/indexes/*/guided-answer",
    async (route) => {
      received.push(route.request().postDataJSON());
      await route.fulfill({
        json:
          received.length === 1
            ? protocol
            : {
                ...protocol,
                status: "limited_answer",
                stop_reason: "repeated_query",
                questions: [],
                trace: [
                  {
                    stage: "retrieval",
                    search: 1,
                    query: "RD_TIMEOUT",
                    new_evidence: 1,
                    total_evidence: 1,
                  },
                  { stage: "stop", reason: "repeated_query" },
                ],
              },
      });
    },
  );
  const button = page.getByRole("button", {
    name: "分步证据问答",
    exact: true,
  });
  await button.click();
  await expect(page.locator(".guided-questions")).toContainText(
    "请提供具体错误现象",
  );
  await page.getByLabel("补充信息（分步问答）").fill("超时，尚无当前日志。");
  await expect(page.locator(".guided-panel")).toHaveCount(0);
  await button.click();
  await expect(page.locator(".guided-panel")).toContainText(
    "查询重复，停止继续检索",
  );
  expect(received[1]!.query).toBe("RD_TIMEOUT");
  expect(received[1]!.clarification).toBe("超时，尚无当前日志。");
  expect(received[1]!.product_version).toBe("1.1");
});

test("REQ-1008: 冲突文本转义、迟到响应和失败用量", async ({ page }) => {
  await login(page);
  await page.getByLabel("检索问题").fill("RD_TIMEOUT");
  const button = page.getByRole("button", {
    name: "分步证据问答",
    exact: true,
  });
  const conflict = {
    ...protocol,
    status: "conflict",
    stop_reason: "conflicting_evidence",
    questions: ["请核对来源。"],
    conflicts: [
      {
        topic: "默认值冲突",
        reason: "待核对",
        citations: [
          {
            context_id: "A",
            evidence_id: "A",
            quote: '<img src=x onerror="window.injected=true">',
            start: 0,
          },
          { context_id: "B", evidence_id: "B", quote: "另一份原文", start: 0 },
        ],
      },
    ],
  };
  await page.route("**/api/retrieval/indexes/*/guided-answer", (route) =>
    route.fulfill({ json: conflict }),
  );
  await button.click();
  await expect(page.locator(".guided-conflict")).toContainText("<img src=x");
  await expect(page.locator(".guided-panel img")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "回查冲突原文" })).toHaveCount(
    2,
  );
  await page.unroute("**/api/retrieval/indexes/*/guided-answer");
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let entered!: () => void;
  const arrival = new Promise<void>((resolve) => {
    entered = resolve;
  });
  await page.route(
    "**/api/retrieval/indexes/*/guided-answer",
    async (route) => {
      entered();
      await gate;
      await route.fulfill({ json: conflict });
    },
  );
  await button.click();
  await arrival;
  await page
    .getByRole("combobox", { name: "产品版本", exact: true })
    .selectOption("2.0");
  release();
  await expect(button).toBeEnabled();
  await expect(page.locator(".guided-panel")).toHaveCount(0);
  await page.unroute("**/api/retrieval/indexes/*/guided-answer");
  await page.route("**/api/retrieval/indexes/*/guided-answer", (route) =>
    route.fulfill({
      status: 503,
      json: {
        error: { code: "MODEL_TIMEOUT", message: "分步模型超时。" },
        trace: [{ stage: "rewrite", reason: "规划完成" }],
        usage: { ...protocol.usage, unknown_usage_calls: 1 },
      },
    }),
  );
  await button.click();
  await expect(page.getByRole("alert")).toContainText("分步模型超时");
  await expect(page.locator(".guided-panel")).toContainText("1 次用量未知调用");
  await expect(page.locator(".answer-panel")).toHaveCount(0);
});

test("REQ-1006/1008: 真实模型分步回答、原文回查与手机", async ({ page }) => {
  test.setTimeout(240000);
  page.on("response", async (response) => {
    if (!response.url().endsWith("/guided-answer")) return;
    const body = await response.json();
    appendFileSync(
      new URL(
        "../../docs/verification/phase-4-round-2/browser-usage.jsonl",
        import.meta.url,
      ),
      JSON.stringify({
        at_utc: new Date().toISOString(),
        http_status: response.status(),
        status: body.status,
        stage: body.stage,
        stop_reason: body.stop_reason,
        usage: body.usage,
      }) + "\n",
      "utf8",
    );
  });
  await login(page);
  await page.getByLabel("案例", { exact: true }).uncheck();
  await page.getByLabel("日志", { exact: true }).uncheck();
  await page
    .getByLabel("检索问题")
    .fill(
      "RelayDesk 1.1 RD_CONFIG_INVALID 应核对哪些配置？仅解释文档，不确认当前客户根因。",
    );
  await page.getByLabel("扩展章节上下文").check();
  await page.getByRole("button", { name: "分步证据问答", exact: true }).click();
  await expect(page.locator(".answer-panel")).toBeVisible({ timeout: 210000 });
  await expect(page.locator(".guided-panel")).toContainText("原始问题");
  await expect(page.locator(".guided-panel")).toContainText("检索改写");
  await expect(page.locator(".answer-claim").first()).toBeVisible();
  await page
    .getByRole("button", { name: "回查引用原文", exact: true })
    .first()
    .click();
  await expect(
    page.getByRole("heading", { name: "原文预览", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: fileURLToPath(
      new URL(
        "../../docs/verification/phase-4-round-2/guided-desktop.png",
        import.meta.url,
      ),
    ),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.locator(".guided-panel").scrollIntoViewIfNeeded();
  await page.screenshot({
    path: fileURLToPath(
      new URL(
        "../../docs/verification/phase-4-round-2/guided-mobile.png",
        import.meta.url,
      ),
    ),
  });
  await page.getByLabel("补充信息（分步问答）").fill("补充当前信息");
  await expect(page.locator(".answer-panel")).toHaveCount(0);
  await expect(page.locator(".guided-panel")).toHaveCount(0);
  await expect(page.locator(".retrieval-preview")).toHaveCount(0);
});

test("REQ-1008: 切换组织后丢弃旧分步响应", async ({ page }) => {
  await login(page);
  await page.getByLabel("检索问题").fill("RD_TIMEOUT");
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let entered!: () => void;
  const arrival = new Promise<void>((resolve) => {
    entered = resolve;
  });
  await page.route(
    "**/api/retrieval/indexes/*/guided-answer",
    async (route) => {
      entered();
      await gate;
      await route.fulfill({ json: protocol });
    },
  );
  await page.getByRole("button", { name: "分步证据问答", exact: true }).click();
  await arrival;
  await page.getByRole("button", { name: "退出登录", exact: true }).click();
  const account = accounts.find((a) => a.username === "support_b")!;
  await page.getByLabel("用户名", { exact: true }).fill(account.username);
  await page.getByLabel("密码", { exact: true }).fill(account.password);
  await page.getByRole("button", { name: "进入工作台", exact: true }).click();
  await page.getByRole("button", { name: /知识与资料/ }).click();
  await page.getByRole("button", { name: "证据检索", exact: true }).click();
  release();
  await expect(
    page.getByText("当前组织暂无检索索引。", { exact: true }),
  ).toBeVisible();
  await expect(page.locator(".guided-panel")).toHaveCount(0);
  await expect(page.locator(".answer-panel")).toHaveCount(0);
});
