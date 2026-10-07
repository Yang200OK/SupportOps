import { closeDetails, openImport } from "./ui-navigation";
import { expect, test, type Page } from "@playwright/test";
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

// 本地账号仅用于登录，公开证据不记录密码和会话。
const accounts = JSON.parse(
  readFileSync(
    new URL("../../local/demo-accounts.json", import.meta.url),
    "utf8",
  ),
).accounts as { username: string; password: string }[];
const evidence = new URL(
  process.env.SUPPORTOPS_CHUNK_EVIDENCE_PATH ??
    "../../docs/verification/ui-refinement-before-phase-7/",
  import.meta.url,
);
async function login(page: Page, username: string) {
  const account = accounts.find((a) => a.username === username)!;
  await page.goto("/");
  await page.getByLabel("用户名", { exact: true }).fill(account.username);
  await page.getByLabel("密码", { exact: true }).fill(account.password);
  await page.getByRole("button", { name: "进入工作台" }).click();
  await page.getByRole("button", { name: /知识与资料/ }).click();
}
test("REQ-401/403/404/405/407: 固定快照、结构、引用、修订和会话", async ({
  page,
}) => {
  const checks: string[] = [],
    errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.name));
  await login(page, "support_a");
  const key = `chunks-${Date.now()}`;
  const table =
    "| 参数 | 值 |\n| --- | --- |\n" +
    Array.from(
      { length: 12 },
      (_, i) => `| timeout_${i} | ${2000 + i} |\n`,
    ).join("");
  const code =
    "```ini\n" +
    Array.from({ length: 14 }, (_, i) => `timeout_${i}=2000\n`).join("") +
    "```\n";
  const original =
    "# 中文配置\n\nRD_TIMEOUT <script>window.__chunk_xss=1</script>\n\n## 参数\n\n" +
    table +
    "\n## 代码\n\n" +
    code +
    "\n## 说明\n\n" +
    "中文说明。".repeat(200);
  await openImport(page);
  await page.getByLabel("来源标识", { exact: true }).fill(key);
  await page.getByLabel("资料标题", { exact: true }).fill("结构切片验收资料");
  const upload = async (text: string) => {
    await openImport(page);
    await page.getByLabel("原文文件", { exact: true }).setInputFiles({
      name: "chunks.md",
      mimeType: "text/markdown",
      buffer: Buffer.from(text),
    });
    await openImport(page);
    await page.getByRole("button", { name: "导入并解析", exact: true }).click();
    await page.getByRole("button", { name: "结构切片", exact: true }).click();
    await expect(page.locator(".source-text")).toHaveText(text);
    await expect(
      page.getByRole("button", { name: "生成结构切片", exact: true }),
    ).toBeEnabled();
  };
  await upload(original);
  await page.getByLabel("目标字符数", { exact: true }).fill("128");
  await page.getByLabel("正文重叠字符", { exact: true }).fill("20");
  const generate = page.getByRole("button", {
    name: "生成结构切片",
    exact: true,
  });
  await generate.click();
  await expect(page.locator(".chunk-summary")).toContainText("structure-v1");
  await generate.click();
  await expect(
    page.getByText("配置已存在，复用切片快照。", { exact: true }),
  ).toBeVisible();
  await expect(generate).toBeEnabled();
  expect(await page.locator(".snapshot-list button").count()).toBe(1);
  checks.push("explicit_creation_and_idempotent_snapshot");
  const tables = page
    .locator(".chunk-item")
    .filter({ hasText: "表头：参数 / 值" });
  expect(await tables.count()).toBeGreaterThan(1);
  for (const item of await tables.all())
    expect(await item.locator("pre").textContent()).toMatch(
      /^\| 参数 \| 值 \|\n\| --- \| --- \|/,
    );
  await tables
    .first()
    .getByRole("button", { name: /预览切片/ })
    .click();
  await expect(page.locator(".citation-preview")).toContainText(
    "字面核对通过；语义支持尚未验证",
  );
  expect(await page.locator(".citation-part").count()).toBe(2);
  for (const part of await page.locator(".citation-part mark").all())
    expect(original.includes((await part.textContent())!)).toBe(true);
  const oldQuote = await page.locator(".citation-preview").textContent();
  checks.push("table_headers_and_multiple_exact_source_spans");
  const codes = page
    .locator(".chunk-item")
    .filter({ hasText: "代码语言：ini" });
  expect(await codes.count()).toBeGreaterThan(1);
  for (const item of await codes.all())
    expect(await item.locator("pre").textContent()).toMatch(
      /^```ini\n[\s\S]*```\n$/,
    );
  await codes
    .first()
    .getByRole("button", { name: /预览切片/ })
    .click();
  await expect(page.locator(".citation-part")).toHaveCount(3);
  await page.locator(".citation-preview summary").click();
  await expect(page.locator(".parent-text")).toContainText("## 代码");
  checks.push("code_language_fences_and_parent_context");
  expect(await page.locator(".chunk-panel script").count()).toBe(0);
  expect(
    await page.evaluate(() =>
      Boolean((window as unknown as Record<string, unknown>).__chunk_xss),
    ),
  ).toBe(false);
  checks.push("escaped_source_content");
  await page.getByRole("button", { name: "下一组切片", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "上一组切片", exact: true }),
  ).toBeEnabled();
  expect(await page.locator(".citation-preview").count()).toBe(0);
  await page.getByRole("button", { name: "上一组切片", exact: true }).click();
  checks.push("pagination_clears_previous_preview");
  await page.getByLabel("目标字符数", { exact: true }).fill("256");
  await generate.click();
  await expect(page.locator(".snapshot-list button")).toHaveCount(2);
  const oldPreview = async () => {
    await page
      .locator(".snapshot-list button")
      .filter({ hasText: "128 / 20" })
      .click();
    await page
      .locator(".chunk-item")
      .filter({ hasText: "表头：参数 / 值" })
      .first()
      .getByRole("button", { name: /预览切片/ })
      .click();
    await expect(page.locator(".citation-preview")).toHaveText(oldQuote!);
  };
  await oldPreview();
  checks.push("changed_parameters_produce_independent_snapshot");
  await upload(original.replaceAll("2000", "3000"));
  await expect(
    page.getByText("当前修订尚未生成切片。", { exact: true }),
  ).toBeVisible();
  expect(await page.locator(".citation-preview").count()).toBe(0);
  await page.getByRole("button", { name: "来源与修订", exact: true }).click();
  await page.getByRole("button", { name: "修订 1", exact: true }).click();
  await page.getByRole("button", { name: "结构切片", exact: true }).click();
  await expect(page.locator(".snapshot-list button")).toHaveCount(2);
  await oldPreview();
  checks.push("revision_switch_clears_preview_and_old_citation_survives");
  await page.locator(".citation-preview").scrollIntoViewIfNeeded();
  await page.screenshot({
    path: fileURLToPath(new URL("citation-desktop.png", evidence)),
  });
  await page.screenshot({
    path: fileURLToPath(new URL("chunks-desktop.png", evidence)),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator(".citation-preview").scrollIntoViewIfNeeded();
  await page.screenshot({
    path: fileURLToPath(new URL("citation-mobile.png", evidence)),
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: fileURLToPath(new URL("chunks-mobile.png", evidence)),
    fullPage: true,
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  checks.push("desktop_and_mobile_no_horizontal_overflow");
  for (const format of ["pdf", "json"]) {
    await openImport(page);
    await page.getByLabel("来源标识", { exact: true }).fill(`${key}-${format}`);
    await page
      .getByLabel("资料标题", { exact: true })
      .fill(`切片 ${format} 资料`);
    await page.getByLabel("导入版本", { exact: true }).selectOption("2.0");
    if (format === "json")
      await page
        .getByLabel("资料来源类型", { exact: true })
        .selectOption("synthetic_case");
    await page
      .getByLabel("原文文件", { exact: true })
      .setInputFiles(
        fileURLToPath(
          new URL(
            `../../data/relaydesk/2.0/${format === "pdf" ? "quick-reference.pdf" : "reported-symptom.json"}`,
            import.meta.url,
          ),
        ),
      );
    await openImport(page);
    await page.getByRole("button", { name: "导入并解析", exact: true }).click();
    await page.getByRole("button", { name: "结构切片", exact: true }).click();
    await expect(page.locator(".document-detail h3")).toHaveText(
      `切片 ${format} 资料`,
    );
    await expect(generate).toBeEnabled();
    await generate.click();
    await expect(page.locator(".chunk-item").first()).toBeVisible();
    await page
      .locator(".chunk-item")
      .first()
      .getByRole("button", { name: /预览切片/ })
      .click();
    await expect(page.locator(".citation-preview")).toContainText(
      format === "pdf" ? "第 1 页（提取文本）" : "字段 /title（解码文本）",
    );
  }
  checks.push("pdf_page_and_json_pointer_citations");
  const token = await page.evaluate(() =>
    sessionStorage.getItem("supportops.session.v1"),
  );
  expect(
    (
      await page.request.post("http://127.0.0.1:8010/api/auth/logout", {
        headers: { Authorization: `Bearer ${token}` },
      })
    ).status(),
  ).toBe(204);
  await generate.click();
  await expect(
    page.getByRole("heading", { name: "登录支持工作台" }),
  ).toBeVisible();
  expect(await page.locator(".chunk-panel").count()).toBe(0);
  await login(page, "support_b");
  expect(await page.locator(".documents-panel").textContent()).not.toContain(
    key,
  );
  checks.push("expired_session_and_cross_org_clear_sources");
  expect(errors).toEqual([]);
  writeFileSync(
    new URL("chunks-browser.json", evidence),
    JSON.stringify(
      {
        browser: "msedge",
        transport: "real_http",
        checks,
        pageerror: errors,
        model_calls: 0,
        executed_at_utc: new Date().toISOString(),
      },
      null,
      2,
    ) + "\n",
    "utf8",
  );
});
