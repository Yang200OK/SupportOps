import { closeDetails, openImport } from "./ui-navigation";
import { expect, test, type Page } from "@playwright/test";
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const accounts: { username: string; password: string }[] = JSON.parse(
  readFileSync(
    new URL("../../local/demo-accounts.json", import.meta.url),
    "utf8",
  ),
).accounts;
const evidence = new URL(
  process.env.SUPPORTOPS_DOCUMENT_EVIDENCE_PATH ??
    "../../docs/verification/ui-refinement-before-phase-7/",
  import.meta.url,
);
async function login(page: Page, username: string) {
  const account = accounts.find((a) => a.username === username)!;
  await page.goto("/");
  await page.getByLabel("用户名", { exact: true }).fill(account.username);
  await page.getByLabel("密码", { exact: true }).fill(account.password);
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(page.getByText("数据库已就绪")).toBeVisible();
  await page.getByRole("button", { name: /知识与资料/ }).click();
  await expect(
    page.getByRole("heading", { name: "知识与资料", exact: true }),
  ).toBeVisible();
}

test("REQ-301/304/305/306/307: 真实资料导入、修订、失败、原文、版本与会话隔离", async ({
  page,
}) => {
  const checks: string[] = [];
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.name));
  await login(page, "support_a");
  const key = `browser-${Date.now()}`;
  const title = `浏览器资料 ${key}`;
  const original =
    "# 中文配置\n\nRD_TIMEOUT\n\n| 参数 | 值 |\n| --- | --- |\n| timeout_ms | 2000 |\n\n```ini\ntimeout_ms=2000\n```\n<script>window.__doc_xss=1</script>\n";
  await openImport(page);
  await page.getByLabel("来源标识", { exact: true }).fill(key);
  await page.getByLabel("资料标题", { exact: true }).fill(title);
  await openImport(page);
  await page.getByLabel("原文文件", { exact: true }).setInputFiles({
    name: "browser.md",
    mimeType: "text/markdown",
    buffer: Buffer.from(original),
  });
  await openImport(page);
  await page.getByRole("button", { name: "导入并解析", exact: true }).click();
  await expect(page.locator(".source-text")).toHaveText(original);
  await expect(page.locator(".source-block")).toContainText([
    "heading",
    "paragraph",
    "table",
    "code",
  ]);
  expect(await page.locator(".source-text script").count()).toBe(0);
  expect(
    await page.evaluate(() =>
      Boolean((window as unknown as Record<string, unknown>).__doc_xss),
    ),
  ).toBe(false);
  checks.push("markdown_chinese_error_code_table_code_and_escaped_text");
  await openImport(page);
  await page.getByRole("button", { name: "导入并解析", exact: true }).click();
  await expect(
    page.getByText("内容已存在，复用原修订。", { exact: true }),
  ).toBeVisible();
  const changed = original.replace("2000", "3000");
  await openImport(page);
  await page.getByLabel("原文文件", { exact: true }).setInputFiles({
    name: "browser.md",
    mimeType: "text/markdown",
    buffer: Buffer.from(changed),
  });
  await openImport(page);
  await page.getByRole("button", { name: "导入并解析", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "修订 2", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "修订 1", exact: true }).click();
  await expect(page.locator(".source-text")).toHaveText(original);
  checks.push("idempotent_import_new_revision_and_old_revision_text");
  const pending = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载本修订原文" }).click();
  const download = await pending;
  expect(download.suggestedFilename()).toBe("browser.md");
  const stream = await download.createReadStream();
  const chunks = [];
  for await (const chunk of stream!) chunks.push(chunk);
  expect(Buffer.concat(chunks).toString("utf8")).toBe(original);
  checks.push("original_download_exact_bytes");
  await page.screenshot({
    path: fileURLToPath(new URL("documents-desktop.png", evidence)),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: fileURLToPath(new URL("documents-mobile.png", evidence)),
    fullPage: true,
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  checks.push("desktop_mobile_no_horizontal_overflow");
  await openImport(page);
  await page.getByLabel("来源标识", { exact: true }).fill(key + "-pdf");
  await page.getByLabel("资料标题", { exact: true }).fill("浏览器 PDF 速查");
  await page.getByLabel("导入版本", { exact: true }).selectOption("2.0");
  await page
    .getByLabel("原文文件", { exact: true })
    .setInputFiles(
      fileURLToPath(
        new URL(
          "../../data/relaydesk/2.0/quick-reference.pdf",
          import.meta.url,
        ),
      ),
    );
  await openImport(page);
  await page.getByRole("button", { name: "导入并解析", exact: true }).click();
  await expect(page.locator(".source-text")).toContainText(
    "downstream_timeout_ms",
  );
  await expect(page.locator(".source-block")).toContainText("第 1 页");
  checks.push("real_text_pdf_page_number_and_chinese");
  await openImport(page);
  await page.getByLabel("来源标识", { exact: true }).fill(key + "-json");
  await page.getByLabel("资料标题", { exact: true }).fill("浏览器案例 JSON");
  await page
    .getByLabel("资料来源类型", { exact: true })
    .selectOption("synthetic_case");
  await page
    .getByLabel("原文文件", { exact: true })
    .setInputFiles(
      fileURLToPath(
        new URL(
          "../../data/relaydesk/2.0/reported-symptom.json",
          import.meta.url,
        ),
      ),
    );
  await openImport(page);
  await page.getByRole("button", { name: "导入并解析", exact: true }).click();
  await expect(page.locator(".source-block")).toContainText([
    "/title",
    "/description",
  ]);
  checks.push("structured_json_symptoms_and_field_locations");
  await openImport(page);
  await page.getByLabel("来源标识", { exact: true }).fill(key + "-v1");
  await page.getByLabel("资料标题", { exact: true }).fill("浏览器 1.0 配置");
  await page
    .getByLabel("资料来源类型", { exact: true })
    .selectOption("demo_product");
  await page.getByLabel("导入版本", { exact: true }).selectOption("1.0");
  await page
    .getByLabel("原文文件", { exact: true })
    .setInputFiles(
      fileURLToPath(
        new URL("../../data/relaydesk/1.0/configuration.md", import.meta.url),
      ),
    );
  await openImport(page);
  await page.getByRole("button", { name: "导入并解析", exact: true }).click();
  await expect(page.locator(".source-text")).toContainText(
    "delivery_timeout_ms=2000",
  );
  checks.push("third_version_configuration_import");
  // 导入完成会自动关闭导入弹窗并打开详情，等待旧弹窗退场后再定位关闭按钮。
  await expect(
    page.getByRole("dialog", { name: "导入资料", exact: true }),
  ).not.toBeVisible();
  await closeDetails(page);
  await page.getByLabel("筛选版本", { exact: true }).selectOption("1.0");
  expect(
    await page.locator(".documents-panel tbody tr").count(),
  ).toBeGreaterThan(0);
  await expect
    .poll(async () => {
      const values = await page
        .locator(".documents-panel tbody tr td:nth-child(2)")
        .allTextContents();
      return values.length > 0 && values.every((v) => v.trim() === "1.0");
    })
    .toBe(true);
  checks.push("version_filter");
  await closeDetails(page);
  await page.getByLabel("筛选版本", { exact: true }).selectOption("");
  await openImport(page);
  await page.getByLabel("来源标识", { exact: true }).fill(key + "-failed");
  await page.getByLabel("资料标题", { exact: true }).fill("浏览器失败资料");
  await openImport(page);
  await page.getByLabel("原文文件", { exact: true }).setInputFiles({
    name: "broken.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-broken"),
  });
  await openImport(page);
  await page.getByRole("button", { name: "导入并解析", exact: true }).click();
  await expect(page.locator(".document-detail [role=alert]")).toContainText(
    "INVALID_PDF",
  );
  expect(await page.locator(".source-text").count()).toBe(0);
  checks.push("failed_pdf_saved_and_no_usable_text");
  // 等待导入后的列表读取完成，再撤销会话；否则该读取可能先触发 401 并卸载筛选框。
  await expect(page.getByLabel("筛选版本", { exact: true })).toBeEnabled();
  const token = await page.evaluate(() =>
    sessionStorage.getItem("supportops.session.v1"),
  );
  const response = await page.request.post(
    "http://127.0.0.1:8010/api/auth/logout",
    { headers: { Authorization: `Bearer ${token}` } },
  );
  expect(response.status()).toBe(204);
  await closeDetails(page);
  await page.getByLabel("筛选版本", { exact: true }).selectOption("1.1");
  await expect(
    page.getByRole("heading", { name: "登录支持工作台" }),
  ).toBeVisible();
  expect(await page.locator(".documents-panel").count()).toBe(0);
  checks.push("expired_session_clears_document_content");
  await login(page, "support_b");
  expect(await page.locator(".documents-panel").textContent()).not.toContain(
    title,
  );
  await closeDetails(page);
  await page.getByRole("button", { name: "退出登录" }).click();
  checks.push("cross_org_ui_does_not_show_a_documents");
  expect(errors).toEqual([]);
  writeFileSync(
    new URL("regression-documents-browser.json", evidence),
    JSON.stringify(
      {
        browser: "msedge",
        transport: "real_http",
        checks,
        pageerror: errors,
        executed_at_utc: new Date().toISOString(),
      },
      null,
      2,
    ) + "\n",
    "utf8",
  );
});
