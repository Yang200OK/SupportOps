import { closeDetails } from "./ui-navigation";
import { expect, test } from "@playwright/test";
import { readFileSync, writeFileSync } from "node:fs";

const folder = new URL(
  "../../docs/verification/phase-6-round-3/",
  import.meta.url,
);
const seed = JSON.parse(
  readFileSync(new URL("seed-attempt1.json", folder), "utf8"),
);
const accounts = JSON.parse(
  readFileSync(
    new URL("../../local/demo-accounts.json", import.meta.url),
    "utf8",
  ),
).accounts as { username: string; password: string }[];

test("REQ-1706: 真实方法创建、报告、批准、发布与撤销，零模型", async ({
  page,
}) => {
  const account = accounts.find((a) => a.username === "support_a")!;
  let modelPosts = 0;
  page.on("request", (r) => {
    if (r.method() === "POST" && r.url().includes("hypothesis-investigations"))
      modelPosts++;
  });
  await page.goto("/");
  await page.getByLabel("用户名", { exact: true }).fill(account.username);
  await page.getByLabel("密码", { exact: true }).fill(account.password);
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(page.locator(".ticket-title").first()).toBeVisible();
  await page.getByRole("button", { name: "经验与 Skill" }).click();
  await page.getByRole("button", { name: "生成方法草稿", exact: true }).click();
  const candidate = seed.retrospectives[0].candidate;
  const choice = page.getByLabel("方法来源候选");
  await expect(
    choice.locator(`option[value="${candidate.candidate_id}"]`),
  ).toHaveCount(1);
  await choice.selectOption(candidate.candidate_id);
  const created = page.waitForResponse(
    (r) =>
      r.url().endsWith("/api/skill-drafts") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "确认生成草稿" }).click();
  const response = await created;
  expect(response.status()).toBe(201);
  const original = await response.json();
  await expect(page.locator(".publication-detail")).toContainText(
    original.payload_sha256,
  );
  await page.getByRole("button", { name: "方法与来源", exact: true }).click();
  await page.getByRole("button", { name: "查看方法经验来源" }).click();
  await expect(page.locator(".publication-detail")).toContainText(
    "source_investigation_id",
  );
  await page.getByRole("button", { name: "回归与审批", exact: true }).click();
  const reg = page.waitForResponse(
    (r) => r.url().endsWith("/regressions") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "运行固定回归" }).click();
  const report = await (await reg).json();
  expect(report.report.passed).toBe(true);
  await page
    .getByLabel("方法审批与发布原因")
    .fill("Edge 认证测试操作者核对绑定，非专家根因审定");
  await page.getByRole("button", { name: "批准方法", exact: true }).click();
  await expect(page.locator(".publication-detail")).toContainText("已批准");
  await page.getByRole("button", { name: "显式发布方法" }).click();
  await expect(page.locator(".publication-detail")).toContainText("已发布");
  await page.screenshot({
    path: new URL(
      "regression-publications-live-publication-desktop.png",
      new URL(
        "../../docs/verification/ui-refinement-before-phase-7/",
        import.meta.url,
      ),
    ).pathname.replace(/^\//, ""),
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator(".publication-detail").scrollIntoViewIfNeeded();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: new URL(
      "regression-publications-live-publication-mobile.png",
      new URL(
        "../../docs/verification/ui-refinement-before-phase-7/",
        import.meta.url,
      ),
    ).pathname.replace(/^\//, ""),
  });
  const revoked = page.waitForResponse(
    (r) => r.url().endsWith("/revoke") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "撤销发布方法" }).click();
  const final = await (await revoked).json();
  expect(final.status).toBe("revoked");
  expect(final.payload_sha256).toBe(original.payload_sha256);
  expect(modelPosts).toBe(0);
  writeFileSync(
    new URL(
      "browser-publication-lifecycle.json",
      new URL(
        "../../docs/verification/ui-refinement-before-phase-7/",
        import.meta.url,
      ),
    ),
    JSON.stringify(
      {
        original,
        final,
        model_calls: modelPosts,
        human_semantic_reviewed: false,
      },
      null,
      2,
    ) + "\n",
    "utf8",
  );
});
