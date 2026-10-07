import { closeDetails } from "./ui-navigation";
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
}
test("REQ-1106: 截图入口必须存在", async ({ page }) => {
  await login(page);
  await page.getByRole("button", { name: /知识与资料/ }).click();
  await page.getByRole("button", { name: "证据检索", exact: true }).click();
  await expect(
    page.getByLabel("RelayDesk 截图 PNG", { exact: true }),
  ).toBeVisible();
});

async function knowledge(page: import("@playwright/test").Page) {
  await login(page);
  await page.getByRole("button", { name: /知识与资料/ }).click();
  await page.getByRole("button", { name: "证据检索", exact: true }).click();
  await expect(
    page.getByLabel("RelayDesk 截图 PNG", { exact: true }),
  ).toBeEnabled();
}
const fixture = fileURLToPath(
  new URL("../../data/screenshots/relaydesk-config.png", import.meta.url),
);
// 下列对象只核对页面协议，不能作为实际 OCR 或回答质量。
const extracted = {
  width: 1100,
  height: 500,
  image_sha256: "protocol-only",
  sent_image_sha256: "protocol-only",
  extraction: {
    recognized: true,
    product: "RelayDesk",
    product_version: "1.1",
    error_code: "RD_CONFIG_INVALID",
    visible_lines: ['<img src=x onerror="window.injected=true">'],
    uncertainty: "请核对像素。",
  },
  usage: {
    requested_model: "protocol-only",
    input_tokens: 10,
    output_tokens: 2,
  },
  limitation: "识别不是事实验证。",
};

test("REQ-1106: 确认、版本不自动切换、文本转义和范围清空（协议）", async ({
  page,
}) => {
  await knowledge(page);
  await page
    .getByRole("combobox", { name: "产品版本", exact: true })
    .selectOption("1.0");
  await page.route("**/api/rag/screenshots/extract", (route) =>
    route.fulfill({ json: extracted }),
  );
  await page
    .getByLabel("RelayDesk 截图 PNG", { exact: true })
    .setInputFiles(fixture);
  await page.getByRole("button", { name: "识别截图", exact: true }).click();
  await expect(page.getByLabel("截图识别待确认内容")).toBeVisible();
  await expect(page.getByLabel("补充信息（分步问答）")).toHaveValue("");
  await expect(page.locator(".screenshot-panel img")).toHaveCount(0);
  await page.getByRole("button", { name: "确认并加入补充信息" }).click();
  await expect(page.getByLabel("补充信息（分步问答）")).toHaveValue(
    /<img src=x/,
  );
  await expect(
    page.getByRole("combobox", { name: "产品版本", exact: true }),
  ).toHaveValue("1.0");
  await page
    .getByRole("combobox", { name: "产品版本", exact: true })
    .selectOption("2.0");
  await expect(page.locator(".screenshot-result")).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});

test("REQ-1106: 换图 / 范围后拒绝迟到识别、错误不保留成功（协议）", async ({
  page,
}) => {
  await knowledge(page);
  let release!: () => void, entered!: () => void;
  const gate = new Promise<void>((r) => (release = r)),
    arrival = new Promise<void>((r) => (entered = r));
  await page.route("**/api/rag/screenshots/extract", async (route) => {
    entered();
    await gate;
    await route.fulfill({ json: extracted });
  });
  await page
    .getByLabel("RelayDesk 截图 PNG", { exact: true })
    .setInputFiles(fixture);
  await page.getByRole("button", { name: "识别截图", exact: true }).click();
  await arrival;
  const late = page.waitForResponse((r) =>
    r.url().endsWith("/screenshots/extract"),
  );
  await page
    .getByLabel("RelayDesk 截图 PNG", { exact: true })
    .setInputFiles(
      fileURLToPath(
        new URL(
          "../../data/screenshots/relaydesk-injection.png",
          import.meta.url,
        ),
      ),
    );
  release();
  await late;
  await expect(page.locator(".screenshot-result")).toHaveCount(0);
  await page
    .getByRole("combobox", { name: "产品版本", exact: true })
    .selectOption("2.0");
  await expect(page.locator(".screenshot-result")).toHaveCount(0);
  await page.unroute("**/api/rag/screenshots/extract");
  await page.route("**/api/rag/screenshots/extract", (route) =>
    route.fulfill({
      status: 503,
      json: {
        error: { code: "MODEL_TIMEOUT", message: "截图模型超时。" },
        usage: {
          known_model_calls: 0,
          unknown_usage_calls: 1,
          input_tokens: 0,
          output_tokens: 0,
          cost_cny: null,
        },
      },
    }),
  );
  await page
    .getByLabel("RelayDesk 截图 PNG", { exact: true })
    .setInputFiles(fixture);
  await page.getByRole("button", { name: "识别截图", exact: true }).click();
  await expect(page.locator(".screenshot-panel [role=alert]")).toContainText(
    "截图模型超时",
  );
  await expect(page.locator(".screenshot-panel")).toContainText(
    "用量未知调用 1",
  );
  await expect(page.locator(".screenshot-result")).toHaveCount(0);
});

test("REQ-1107: 真实报告格式 / 全分母与手机展示", async ({ page }) => {
  await login(page);
  await page.getByRole("button", { name: /评测准备/ }).click();
  await page.getByRole("button", { name: "打开离线报告" }).click();
  await page
    .getByLabel("回答评测报告 JSON", { exact: true })
    .setInputFiles(
      fileURLToPath(
        new URL(
          "../../docs/verification/phase-4-round-3/answer-report-v2-corrected-rules.json",
          import.meta.url,
        ),
      ),
    );
  await expect(page.locator(".answer-evaluation-result")).toContainText(
    "全部 16 · 完成 9 · 失败 3 · 未执行 4",
  );
  await expect(page.locator(".answer-evaluation-result")).toContainText(
    "规则覆盖不是语义准确率",
  );
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "../docs/verification/ui-refinement-before-phase-7/report-mobile.png",
    animations: "disabled",
    fullPage: true,
  });
  await closeDetails(page);
  await expect(page.locator("main .report-overview")).toHaveText(
    "全部 16 · 完成 9 · 失败 3 · 未执行 4",
  );
});

test("REQ-1106: 真实像素识别、确认后分步回答与引用回查", async ({ page }) => {
  test.setTimeout(240000);
  await knowledge(page);
  await page
    .getByRole("combobox", { name: "检索模式", exact: true })
    .selectOption("bm25");
  await page
    .getByRole("combobox", { name: "指定资料", exact: true })
    .selectOption({ label: "RelayDesk 1.1 配置说明" });
  await page.getByLabel("扩展章节上下文").check();
  await page
    .getByLabel("检索问题")
    .fill("RelayDesk 1.1 RD_CONFIG_INVALID 的拒绝启动规则是什么？只解释资料。");
  await page
    .getByLabel("RelayDesk 截图 PNG", { exact: true })
    .setInputFiles(fixture);
  const pending = page.waitForResponse(
    (r) => r.url().endsWith("/screenshots/extract"),
    { timeout: 90000 },
  );
  await page.getByRole("button", { name: "识别截图", exact: true }).click();
  const response = await pending,
    body = await response.json();
  appendFileSync(
    fileURLToPath(
      new URL(
        "../../docs/verification/ui-refinement-before-phase-7/browser-live.jsonl",
        import.meta.url,
      ),
    ),
    JSON.stringify({
      case: "screenshot",
      http_status: response.status(),
      response: body,
    }) + "\n",
  );
  expect(response.status()).toBe(200);
  expect(body.extraction.error_code).toBe("RD_CONFIG_INVALID");
  await expect(page.getByLabel("补充信息（分步问答）")).toHaveValue("");
  await page
    .getByLabel("截图识别待确认内容")
    .fill(
      "截图识别（用户确认，尚未验证）：产品 RelayDesk 1.1；错误 RD_CONFIG_INVALID。只查询文档的拒绝启动规则，截图不证明根因。",
    );
  await page.getByRole("button", { name: "确认并加入补充信息" }).click();
  const answerPending = page.waitForResponse(
    (r) => r.url().endsWith("/guided-answer"),
    { timeout: 190000 },
  );
  await page.getByRole("button", { name: "分步证据问答", exact: true }).click();
  const answerResponse = await answerPending,
    guided = await answerResponse.json();
  appendFileSync(
    fileURLToPath(
      new URL(
        "../../docs/verification/ui-refinement-before-phase-7/browser-live.jsonl",
        import.meta.url,
      ),
    ),
    JSON.stringify({
      case: "screenshot-guided",
      http_status: answerResponse.status(),
      response: guided,
    }) + "\n",
  );
  expect(answerResponse.status()).toBe(200);
  expect(guided.answer?.claims.length).toBeGreaterThan(0);
  await page
    .getByRole("button", { name: "回查引用原文", exact: true })
    .first()
    .click();
  await expect(page.locator(".retrieval-preview")).toContainText("来源已核对");
  await page.screenshot({
    path: "../docs/verification/ui-refinement-before-phase-7/screenshot-answer-desktop.png",
    fullPage: true,
  });
});
