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

// 下列截获只核对界面协议与迟到响应，真实模型质量由另一条流程记录。
const protocolAnswer = {
  answer_id: "protocol-only",
  persisted: false,
  status: "reviewed",
  product_version: "1.1",
  contexts: [],
  retrieval: {
    mode: "vector",
    eligible_count: 0,
    items: [],
    latency_ms: 1,
    usage: { model_called: false, input_tokens: 0, cost_cny: null },
  },
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
      citations: [
        {
          context_id: "E1",
          evidence_id: "E1",
          quote: "无关原文",
          start: 0,
          end: 4,
          literal_verified: true,
          context_text_sha256: "protocol-only",
        },
      ],
    },
  ],
  missing_information: ["请补充当前日志。"],
  limitation: "模型语义判断尚未人工复核。",
  latency_ms: 1,
  usage: {
    input_tokens: 10,
    output_tokens: 5,
    known_model_calls: 2,
    unknown_usage_calls: 0,
    cost_cny: null,
  },
};

test("REQ-906: 不支持项的警示、文本转义与迟到响应", async ({ page }) => {
  await login(page);
  await page.getByLabel("检索问题").fill("RD_CONFIG_INVALID");
  const button = page.getByRole("button", {
    name: "生成证据回答",
    exact: true,
  });
  await page.route("**/api/retrieval/indexes/*/answer", (route) =>
    route.fulfill({ json: protocolAnswer }),
  );
  await button.click();
  await expect(page.locator(".answer-claim.unsupported")).toContainText(
    "不应作为已证实事实",
  );
  await expect(page.locator(".claim-text")).toContainText("<img src=x");
  await expect(page.locator(".answer-panel img")).toHaveCount(0);
  await page.getByLabel("检索问题").fill("改变问题");
  await expect(page.locator(".answer-panel")).toHaveCount(0);
  await page.unroute("**/api/retrieval/indexes/*/answer");
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let entered!: () => void;
  const arrival = new Promise<void>((resolve) => {
    entered = resolve;
  });
  await page.route("**/api/retrieval/indexes/*/answer", async (route) => {
    entered();
    await gate;
    await route.fulfill({ json: protocolAnswer });
  });
  await button.click();
  await arrival;
  await page
    .getByRole("combobox", { name: "产品版本", exact: true })
    .selectOption("2.0");
  release();
  await expect(button).toBeEnabled();
  await expect(page.locator(".answer-panel")).toHaveCount(0);
  await page.unroute("**/api/retrieval/indexes/*/answer");
  await page.route("**/api/retrieval/indexes/*/answer", (route) =>
    route.fulfill({
      status: 503,
      json: { error: { code: "MODEL_TIMEOUT", message: "回答模型超时。" } },
    }),
  );
  await button.click();
  await expect(page.getByRole("alert")).toContainText("回答模型超时");
  await expect(page.locator(".answer-panel")).toHaveCount(0);
});

test("REQ-906: 回答入口存在", async ({ page }) => {
  await login(page);
  await expect(
    page.getByRole("button", { name: "生成证据回答", exact: true }),
  ).toBeVisible();
});

test("REQ-907: 真实模型回答、引文原文、手机与范围清空", async ({ page }) => {
  test.setTimeout(180000);
  page.on("response", async (response) => {
    if (!response.url().endsWith("/answer")) return;
    const body = await response.json();
    appendFileSync(
      new URL(
        "../../docs/verification/phase-4-round-1/browser-usage.jsonl",
        import.meta.url,
      ),
      JSON.stringify({
        at_utc: new Date().toISOString(),
        status: response.status(),
        stage: body.stage,
        usage: body.usage,
        claims: body.claims?.map(
          (c: { claim_id: string; support: unknown }) => ({
            claim_id: c.claim_id,
            support: c.support,
          }),
        ),
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
      "RelayDesk 1.1 启动报 RD_CONFIG_INVALID，应核对哪些配置？我没有当前配置和日志，请不要确认根因。",
    );
  await page.getByRole("button", { name: "生成证据回答", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "证据回答", exact: true }),
  ).toBeVisible({ timeout: 150000 });
  await expect(page.locator(".answer-claim").first()).toBeVisible();
  await expect(page.locator(".answer-panel")).toContainText("模型语义判断");
  await expect(page.locator(".answer-panel")).toContainText("尚未人工复核");
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
        "../../docs/verification/phase-4-round-1/answer-desktop.png",
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
  await page.locator(".answer-panel").scrollIntoViewIfNeeded();
  await page.screenshot({
    path: fileURLToPath(
      new URL(
        "../../docs/verification/phase-4-round-1/answer-mobile.png",
        import.meta.url,
      ),
    ),
  });
  await page
    .getByRole("combobox", { name: "产品版本", exact: true })
    .selectOption("2.0");
  await expect(page.locator(".answer-panel")).toHaveCount(0);
  await expect(page.locator(".retrieval-preview")).toHaveCount(0);
});
