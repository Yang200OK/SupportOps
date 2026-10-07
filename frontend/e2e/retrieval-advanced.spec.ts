import { expect, test } from "@playwright/test";
import { readFileSync, appendFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
const accounts = JSON.parse(
  readFileSync(
    new URL("../../local/demo-accounts.json", import.meta.url),
    "utf8",
  ),
).accounts as { username: string; password: string }[];
test("REQ-808: 真实重排序、章节上下文、清空与手机", async ({ page }) => {
  page.on("response", async (response) => {
    if (!response.url().endsWith("/search")) return;
    const body = await response.json();
    appendFileSync(
      new URL(
        "../../docs/verification/phase-3-round-3/browser-usage.jsonl",
        import.meta.url,
      ),
      JSON.stringify({
        at_utc: new Date().toISOString(),
        status: response.status(),
        usage: body.usage,
        rerank_usage: body.rerank_usage,
        retrieval_usage: body.retrieval_usage,
      }) + "\n",
      "utf8",
    );
  });
  const account = accounts.find((a) => a.username === "support_a")!;
  await page.goto("/");
  await page.getByLabel("用户名", { exact: true }).fill(account.username);
  await page.getByLabel("密码", { exact: true }).fill(account.password);
  await page.getByRole("button", { name: "进入工作台" }).click();
  await page.getByRole("button", { name: /知识与资料/ }).click();
  await page.getByRole("button", { name: "证据检索", exact: true }).click();
  await expect(
    page.getByLabel("模型重排序", { exact: true }),
  ).not.toBeChecked();
  await expect(
    page.getByLabel("扩展章节上下文", { exact: true }),
  ).not.toBeChecked();
  await page
    .getByRole("combobox", { name: "检索模式", exact: true })
    .selectOption("rrf");
  await page
    .getByLabel("检索问题")
    .fill("启动报 RD_CONFIG_INVALID，应该核对哪些配置信息？");
  await page.getByLabel("案例", { exact: true }).uncheck();
  await page.getByLabel("日志", { exact: true }).uncheck();
  await page.getByLabel("模型重排序", { exact: true }).check();
  await page.getByLabel("扩展章节上下文", { exact: true }).check();
  await page.getByRole("button", { name: "检索候选", exact: true }).click();
  await expect(page.locator(".retrieval-hit")).toHaveCount(5, {
    timeout: 65000,
  });
  await expect(page.locator(".retrieval-panel")).toContainText("排序模型");
  await expect(page.locator(".retrieval-scores").first()).toContainText(
    "排序分数",
  );
  await page
    .getByRole("button", { name: "查看原文", exact: true })
    .first()
    .click();
  await expect(
    page.getByRole("heading", { name: "章节上下文", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".expanded-context").first()).toContainText(
    "命中锚点",
  );
  await page.locator(".retrieval-panel").screenshot({
    path: fileURLToPath(
      new URL(
        "../../docs/verification/phase-3-round-3/retrieval-advanced-desktop.png",
        import.meta.url,
      ),
    ),
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.locator(".expanded-context").first().scrollIntoViewIfNeeded();
  await page.screenshot({
    path: fileURLToPath(
      new URL(
        "../../docs/verification/phase-3-round-3/retrieval-advanced-mobile.png",
        import.meta.url,
      ),
    ),
  });
  await page.getByLabel("扩展章节上下文", { exact: true }).uncheck();
  await expect(page.locator(".retrieval-hit")).toHaveCount(0);
  await expect(page.locator(".expanded-context")).toHaveCount(0);
});
