import { expect, test, type Page } from "@playwright/test";
import { appendFileSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const evidenceRoot = new URL(
  process.env.SUPPORTOPS_RETRIEVAL_EVIDENCE_PATH ??
    "../../docs/verification/phase-3-round-2/",
  import.meta.url,
);

const accounts = JSON.parse(
  readFileSync(
    new URL("../../local/demo-accounts.json", import.meta.url),
    "utf8",
  ),
).accounts as { username: string; password: string }[];
async function login(page: Page, name: string) {
  const account = accounts.find((a) => a.username === name)!;
  await page.goto("/");
  await page.getByLabel("用户名", { exact: true }).fill(account.username);
  await page.getByLabel("密码", { exact: true }).fill(account.password);
  await page.getByRole("button", { name: "进入工作台" }).click();
  await page.getByRole("button", { name: /知识与资料/ }).click();
  await page.getByRole("button", { name: "证据检索", exact: true }).click();
}
test.beforeEach(async ({ page }, info) => {
  page.on("response", async (response) => {
    if (!response.url().match(/\/api\/retrieval\/indexes\/[^/]+\/search$/))
      return;
    try {
      const data = await response.json();
      appendFileSync(
        new URL("browser-requests.jsonl", evidenceRoot),
        JSON.stringify({
          at_utc: new Date().toISOString(),
          test: info.title,
          status: response.status(),
          request: response.request().postDataJSON(),
          usage: data.usage ?? null,
          mode: data.mode ?? null,
          latency_ms: data.latency_ms ?? null,
        }) + "\n",
        "utf8",
      );
    } catch {}
  });
});
test("REQ-707/708: 三路真实候选、原文、范围、手机与会话", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.name));
  await login(page, "support_a");
  await expect(page.getByLabel("检索索引")).not.toHaveValue("");
  await page
    .getByLabel("检索问题")
    .fill("RD_TIMEOUT downstream_timeout_ms 配置");
  await page.getByLabel("检索模式").selectOption("bm25");
  await page.getByRole("button", { name: "检索候选", exact: true }).click();
  await expect(page.locator(".retrieval-hit")).toHaveCount(5);
  await expect(page.locator(".retrieval-summary")).toContainText("未调用模型");
  await page
    .getByRole("button", { name: "查看原文", exact: true })
    .first()
    .click();
  await expect(page.getByRole("heading", { name: "原文预览" })).toBeVisible();
  await expect(page.locator(".retrieval-preview")).toContainText("来源已核对");
  await page.locator(".retrieval-panel").screenshot({
    path: fileURLToPath(new URL("retrieval-desktop.png", evidenceRoot)),
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: fileURLToPath(new URL("retrieval-mobile.png", evidenceRoot)),
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  for (const mode of ["vector", "rrf"]) {
    await page.getByLabel("检索模式").selectOption(mode);
    await expect(page.locator(".retrieval-hit")).toHaveCount(0);
    await page.getByRole("button", { name: "检索候选", exact: true }).click();
    await expect(page.locator(".retrieval-hit")).toHaveCount(5);
    await expect(page.locator(".retrieval-summary")).toContainText("调用模型");
  }
  await page.getByLabel("资料", { exact: true }).uncheck();
  await page.getByLabel("案例", { exact: true }).uncheck();
  await page.getByRole("button", { name: "检索候选", exact: true }).click();
  await expect(page.locator(".retrieval-hit")).toHaveCount(5);
  await page
    .getByRole("button", { name: "查看原文", exact: true })
    .first()
    .click();
  await expect(page.locator(".retrieval-preview")).toContainText("lab:");
  await page
    .getByRole("combobox", { name: "产品版本", exact: true })
    .selectOption("2.0");
  await expect(page.locator(".retrieval-hit")).toHaveCount(0);
  await expect(page.locator(".retrieval-preview")).toHaveCount(0);
  await page.getByRole("button", { name: "工单工作台", exact: false }).click();
  await page.getByRole("button", { name: /知识与资料/ }).click();
  await expect(page.getByRole("heading", { name: /资料列表/ })).toBeVisible();
  await page.getByRole("button", { name: "证据检索", exact: true }).click();
  await expect(page.locator(".retrieval-hit")).toHaveCount(0);
  await page.getByRole("button", { name: "退出登录" }).click();
  await login(page, "support_b");
  await expect(
    page.getByText("当前组织暂无检索索引。", { exact: true }),
  ).toBeVisible();
  await expect(page.locator(".retrieval-hit")).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("REQ-708: 迟到响应、失败与 HTML 按文本显示（界面协议测试）", async ({
  page,
}) => {
  await login(page, "support_a");
  await page.getByLabel("检索模式").selectOption("bm25");
  await page.getByLabel("检索问题").fill("RD_TIMEOUT");
  const searchButton = page.getByRole("button", {
    name: "检索候选",
    exact: true,
  });
  let release!: () => void;
  let received!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const ready = new Promise<void>((resolve) => {
    received = resolve;
  });
  await page.route("**/api/retrieval/indexes/*/search", async (route) => {
    const response = await route.fetch();
    received();
    await gate;
    await route.fulfill({ response });
  });
  await searchButton.click();
  await ready;
  await page
    .getByRole("combobox", { name: "产品版本", exact: true })
    .selectOption("2.0");
  release();
  await expect(searchButton).toBeEnabled();
  await expect(page.locator(".retrieval-hit")).toHaveCount(0);
  await page.unroute("**/api/retrieval/indexes/*/search");
  await page.route("**/api/retrieval/indexes/*/search", (route) =>
    route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({
        error: { code: "MODEL_TIMEOUT", message: "模型调用失败。" },
      }),
    }),
  );
  await searchButton.click();
  await expect(page.getByRole("alert")).toContainText("模型调用失败");
  await expect(page.locator(".retrieval-hit")).toHaveCount(0);
  await page.unroute("**/api/retrieval/indexes/*/search");
  await page.route("**/api/retrieval/indexes/*/search", async (route) => {
    const response = await route.fetch();
    const data = await response.json();
    data.items = data.items.slice(0, 1);
    data.items[0].text = '<img src=x onerror="window.injected=true">';
    await route.fulfill({ json: data });
  });
  await searchButton.click();
  await expect(page.locator(".retrieval-hit")).toHaveCount(1);
  await expect(page.locator(".retrieval-hit")).toContainText("<img src=x");
  expect(await page.locator(".retrieval-hit img").count()).toBe(0);
});

test("REQ-707: 具体来源真实检索与索引分页界面协议", async ({ page }) => {
  await login(page, "support_a");
  await page
    .getByRole("combobox", { name: "检索模式", exact: true })
    .selectOption("bm25");
  await page.getByLabel("检索问题").fill("配置 RD_TIMEOUT");
  await page.getByLabel("案例", { exact: true }).uncheck();
  await page.getByLabel("日志", { exact: true }).uncheck();
  await page
    .getByRole("combobox", { name: "指定资料", exact: true })
    .selectOption({ label: "RelayDesk 1.1 配置说明" });
  const searchButton = page.getByRole("button", {
    name: "检索候选",
    exact: true,
  });
  let responsePromise = page.waitForResponse(
    (r) => r.url().endsWith("/search") && r.request().method() === "POST",
  );
  await searchButton.click();
  let result = await (await responsePromise).json();
  expect(result.items.length).toBeGreaterThan(0);
  expect(
    result.items.every(
      (hit: { source: { document_id: string } }) =>
        hit.source.document_id === result.scope.document_ids[0],
    ),
  ).toBe(true);
  await expect(page.locator(".retrieval-hit h3").first()).toContainText(
    "配置说明",
  );
  await page.getByLabel("资料", { exact: true }).uncheck();
  await page.getByLabel("日志", { exact: true }).check();
  const experimentSelect = page.getByRole("combobox", {
    name: "指定实验",
    exact: true,
  });
  const experimentId = await experimentSelect
    .locator("option")
    .nth(1)
    .getAttribute("value");
  await experimentSelect.selectOption(experimentId!);
  await page.getByLabel("检索问题").fill("phase");
  responsePromise = page.waitForResponse(
    (r) => r.url().endsWith("/search") && r.request().method() === "POST",
  );
  await searchButton.click();
  result = await (await responsePromise).json();
  expect(result.items.length).toBeGreaterThan(0);
  expect(
    result.items.every(
      (hit: { source: { experiment_id: string } }) =>
        hit.source.experiment_id === experimentId,
    ),
  ).toBe(true);
  await expect(page.locator(".retrieval-hit")).toHaveCount(result.items.length);
  // 当前组织只有一个真实索引；十一项列表由协议截获构造，来源检索仍走真实 API。
  let original: Record<string, unknown>;
  await page.route("**/api/retrieval/indexes?*", async (route) => {
    const offset = Number(
      new URL(route.request().url()).searchParams.get("offset"),
    );
    if (offset === 0) {
      const response = await route.fetch();
      original = (await response.json()).items[0];
    }
    const items =
      offset === 0
        ? [
            original,
            ...Array.from({ length: 9 }, (_, i) => ({
              ...original,
              index_id: `00000000-0000-0000-0000-${String(i + 1).padStart(12, "0")}`,
            })),
          ]
        : [original];
    await route.fulfill({ json: { items, total: 11, offset, limit: 10 } });
  });
  await page.getByRole("button", { name: "刷新索引", exact: true }).click();
  await expect(page.locator(".retrieval-hit")).toHaveCount(0);
  const next = page.getByRole("button", { name: "下一页索引", exact: true });
  const previous = page.getByRole("button", {
    name: "上一页索引",
    exact: true,
  });
  await expect(previous).toBeDisabled();
  await next.click();
  await expect(previous).toBeEnabled();
  await expect(next).toBeDisabled();
  await expect(page.locator(".retrieval-controls")).toContainText("11–11 / 11");
  await previous.click();
  await expect(previous).toBeDisabled();
  await expect(next).toBeEnabled();
  await expect(page.locator(".retrieval-hit")).toHaveCount(0);
});
