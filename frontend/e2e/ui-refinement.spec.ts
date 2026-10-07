import { expect, test, type Page } from "@playwright/test";
import { readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const evidence = new URL(
  "../../docs/verification/ui-refinement-before-phase-7/",
  import.meta.url,
);
mkdirSync(evidence, { recursive: true });
const accounts = JSON.parse(
  readFileSync(
    new URL("../../local/demo-accounts.json", import.meta.url),
    "utf8",
  ),
).accounts as { username: string; password: string }[];
async function login(page: Page) {
  const a = accounts.find((a) => a.username === "support_a")!;
  await page.goto("/");
  await page.getByLabel("用户名", { exact: true }).fill(a.username);
  await page.getByLabel("密码", { exact: true }).fill(a.password);
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(page.locator(".ticket-title").first()).toBeVisible();
}
async function capture(page: Page, name: string) {
  await page.screenshot({
    path: fileURLToPath(new URL(`${name}.png`, evidence)),
    fullPage: !(await page.getByRole("dialog").count()),
    animations: "disabled",
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
}

test("REQ-UI-01/02/03/04/05/06: 真实只读页面的按需详情、关闭返回与紧凑视图", async ({
  page,
}) => {
  const modelRequests: string[] = [];
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("request", (r) => {
    if (
      r.method() === "POST" &&
      /\/api\/(investigations|hypothesis-investigations|answers|guided|action-jobs)/.test(
        r.url(),
      )
    )
      modelRequests.push(new URL(r.url()).pathname);
  });
  await login(page);
  await capture(page, "tickets-desktop");
  const first = page.getByRole("button", { name: "查看", exact: true }).first();
  await first.click();
  const ticket = page.getByRole("dialog", { name: "工单详情", exact: true });
  await expect(ticket).toBeVisible();
  await expect(page.locator("main .investigation-panel")).toHaveCount(0);
  await ticket.getByRole("button", { name: "假设调查", exact: true }).click();
  await expect(ticket.locator(".hypothesis-panel")).toBeVisible();
  await ticket.getByRole("button", { name: "批准动作", exact: true }).click();
  await expect(ticket.locator(".action-panel")).toBeVisible();
  await ticket.getByRole("button", { name: "工单概况", exact: true }).click();
  await capture(page, "ticket-dialog-desktop");
  await page.keyboard.press("Escape");
  await expect(ticket).not.toBeVisible();
  await expect(first).toBeFocused();
  await page.getByRole("button", { name: /02\s*运行记录/ }).click();
  await capture(page, "runs-desktop");
  const runTrigger = page.getByRole("button", { name: "查看记录" }).first();
  await runTrigger.click();
  await expect(
    page.getByRole("dialog", { name: "运行详情", exact: true }),
  ).toContainText("未调用 / 未使用 / 未计费");
  await page.keyboard.press("Escape");
  await expect(
    page.getByRole("dialog", { name: "运行详情", exact: true }),
  ).not.toBeVisible();
  await expect(runTrigger).toBeFocused();
  await page.getByRole("button", { name: /03\s*评测准备/ }).click();
  await expect(page.locator("main textarea")).toHaveCount(0);
  await capture(page, "evaluation-desktop");
  await page.getByRole("button", { name: "编辑与校验数据集" }).click();
  const dataset = page.getByRole("dialog", {
    name: "数据集格式校验",
    exact: true,
  });
  await dataset.getByRole("button", { name: "校验数据集格式" }).click();
  await expect(dataset).toContainText("executed: false");
  await page.keyboard.press("Escape");
  await expect(dataset).not.toBeVisible();
  await page.getByRole("button", { name: /04\s*知识与资料/ }).click();
  await expect(page.locator("main .document-form")).toHaveCount(0);
  await capture(page, "documents-desktop");
  await page.getByRole("button", { name: "导入资料", exact: true }).click();
  await expect(
    page
      .getByRole("dialog", { name: "导入资料", exact: true })
      .getByLabel("原文文件"),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(
    page.getByRole("dialog", { name: "导入资料", exact: true }),
  ).not.toBeVisible();
  await page.getByRole("button", { name: "核对", exact: true }).first().click();
  const doc = page.getByRole("dialog", { name: "原文与解析核对", exact: true });
  await expect(doc).toContainText("原文 SHA-256");
  await capture(page, "document-dialog-desktop");
  await page.keyboard.press("Escape");
  await expect(doc).not.toBeVisible();
  await page.getByRole("button", { name: /05\s*经验与 Skill/ }).click();
  await expect(page.locator("main .publication-panel")).toBeVisible();
  await expect(page.locator("main .memory-panel")).toHaveCount(0);
  await expect(page.locator(".publication-card").first()).toBeVisible();
  await expect(
    page.getByRole("button", { name: "生成方法草稿", exact: true }),
  ).toBeEnabled();
  await capture(page, "skills-desktop");
  await page.getByRole("button", { name: "成对实验", exact: true }).click();
  await expect(page.locator(".memory-pairs-panel")).toContainText("3 / 4");
  await expect(page.locator(".memory-pairs-panel")).toContainText("2 / 4");
  await page.getByRole("button", { name: "查看配对明细" }).click();
  await page.getByRole("button", { name: "查看成对调查" }).first().click();
  const pair = page.getByRole("dialog", { name: "成对调查详情", exact: true });
  await expect(pair.locator(".pair-investigation")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(pair).not.toBeVisible();
  await page.keyboard.press("Escape");
  await expect(
    page.getByRole("dialog", { name: "配对任务与结果", exact: true }),
  ).not.toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await capture(page, "pairs-mobile");
  await page.getByRole("button", { name: "组织方法", exact: true }).click();
  await page.getByRole("button", { name: "查看方法发布" }).first().click();
  await expect(
    page.getByRole("dialog", { name: "组织方法详情", exact: true }),
  ).toBeVisible();
  await expect(
    page
      .getByRole("dialog", { name: "组织方法详情", exact: true })
      .getByRole("button", { name: "返回列表" }),
  ).toBeEnabled();
  await capture(page, "publication-dialog-mobile");
  await page.keyboard.press("Escape");
  await expect(
    page.getByRole("dialog", { name: "组织方法详情", exact: true }),
  ).not.toBeVisible();
  await page.getByRole("button", { name: "工单工作台" }).click();
  await capture(page, "tickets-mobile");
  await page.getByRole("button", { name: "查看", exact: true }).first().click();
  await expect(ticket).toBeVisible();
  await capture(page, "ticket-dialog-mobile");
  expect(errors).toEqual([]);
  expect(modelRequests).toEqual([]);
  writeFileSync(
    new URL("browser-readonly.json", evidence),
    JSON.stringify(
      {
        executed_at: new Date().toISOString(),
        transport: "real_edge_real_http_postgresql",
        status: "passed",
        requests_starting_models: modelRequests,
        page_errors: errors,
        requirements: [
          "REQ-UI-01",
          "REQ-UI-02",
          "REQ-UI-03",
          "REQ-UI-04",
          "REQ-UI-05",
          "REQ-UI-06",
        ],
      },
      null,
      2,
    ) + "\n",
    "utf8",
  );
});

test("REQ-UI-02/03/04/06: 真实资料导入、旧修订原文与离线校验", async ({
  page,
}) => {
  await login(page);
  await page.getByRole("button", { name: "知识与资料" }).click();
  await page.getByRole("button", { name: "导入资料", exact: true }).click();
  const form = page.getByRole("dialog", { name: "导入资料", exact: true });
  const key = `ui-refinement-${Date.now()}`;
  await form.getByLabel("来源标识", { exact: true }).fill(key);
  await form
    .getByLabel("资料标题", { exact: true })
    .fill("界面验收中文配置原文");
  const original =
    "# 配置说明\n\nRD_TIMEOUT\n\n```ini\ntimeout_ms=2000\n```\n<script>window.uiInjected=true</script>\n";
  await form.getByLabel("原文文件", { exact: true }).setInputFiles({
    name: "ui.md",
    mimeType: "text/markdown",
    buffer: Buffer.from(original),
  });
  const imported = page.waitForResponse(
    (r) =>
      r.url().endsWith("/api/documents/import") &&
      r.request().method() === "POST",
  );
  await form.getByRole("button", { name: "导入并解析", exact: true }).click();
  const revision = await (await imported).json();
  const detail = page.getByRole("dialog", {
    name: "原文与解析核对",
    exact: true,
  });
  await expect(detail).toBeVisible();
  await detail.getByRole("button", { name: "解析正文", exact: true }).click();
  await expect(detail.locator(".source-text")).toHaveText(original);
  expect(
    await page.evaluate(() =>
      Boolean((window as unknown as { uiInjected?: boolean }).uiInjected),
    ),
  ).toBe(false);
  await page.keyboard.press("Escape");
  await expect(detail).not.toBeVisible();
  await page.getByRole("button", { name: "导入资料", exact: true }).click();
  await form.getByLabel("原文文件", { exact: true }).setInputFiles({
    name: "ui.md",
    mimeType: "text/markdown",
    buffer: Buffer.from(original.replace("2000", "3000")),
  });
  await form.getByRole("button", { name: "导入并解析", exact: true }).click();
  await expect(
    detail.getByRole("button", { name: "修订 2", exact: true }),
  ).toBeVisible();
  await detail.getByRole("button", { name: "修订 1", exact: true }).click();
  const pending = page.waitForEvent("download");
  await detail
    .getByRole("button", { name: "下载本修订原文", exact: true })
    .click();
  const download = await pending;
  const stream = await download.createReadStream();
  const chunks = [];
  for await (const chunk of stream!) chunks.push(chunk);
  expect(Buffer.concat(chunks).toString("utf8")).toBe(original);
  await detail.getByRole("button", { name: "结构切片", exact: true }).click();
  await expect(
    detail.getByRole("button", { name: "生成结构切片", exact: true }),
  ).toBeVisible();
  await detail.getByRole("button", { name: "解析正文", exact: true }).click();
  await expect(detail.locator(".source-text")).toHaveText(original);
  await capture(page, "document-text-dialog-desktop");
  await page.keyboard.press("Escape");
  await expect(detail).not.toBeVisible();
  await page.getByLabel("筛选版本", { exact: true }).selectOption("2.0");
  await expect(page.locator(".documents-panel tbody")).not.toContainText(key);
  await page.getByRole("button", { name: "运行记录" }).click();
  await page.getByRole("button", { name: "故障实验", exact: true }).click();
  await page
    .getByRole("button", { name: /查看观测 / })
    .first()
    .click();
  const lab = page.getByRole("dialog", { name: "三阶段观测", exact: true });
  await expect(lab).toBeVisible();
  await lab.getByRole("button", { name: "异常观测", exact: true }).click();
  await expect(lab.locator(".experiment-phase:visible")).toHaveCount(1);
  await page.keyboard.press("Escape");
  await expect(lab).not.toBeVisible();
  await page.getByRole("button", { name: "评测准备" }).click();
  await page.getByRole("button", { name: "编辑与校验数据集" }).click();
  const ds = page.getByRole("dialog", { name: "数据集格式校验", exact: true });
  await ds.getByLabel("评测数据集 JSON").fill("{broken}");
  await ds.getByRole("button", { name: "校验数据集格式" }).click();
  await expect(ds.getByRole("alert")).toContainText("JSON 格式无效");
  writeFileSync(
    new URL("browser-documents.json", evidence),
    JSON.stringify(
      {
        status: "passed",
        document_id: revision.document_id,
        revision_id: revision.revision_id,
        original_download_exact: true,
        chinese_preserved: true,
        html_escaped: true,
        phase_observations: true,
        model_calls: 0,
      },
      null,
      2,
    ) + "\n",
    "utf8",
  );
});
