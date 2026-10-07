import { closeDetails } from "./ui-navigation";
import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

const accounts = JSON.parse(
  readFileSync(
    new URL("../../local/demo-accounts.json", import.meta.url),
    "utf8",
  ),
).accounts as { username: string; password: string }[];
async function login(page: Page) {
  const account = accounts.find((a) => a.username === "support_a")!;
  await page.goto("/");
  await page.getByLabel("用户名", { exact: true }).fill(account.username);
  await page.getByLabel("密码", { exact: true }).fill(account.password);
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(
    page.getByRole("heading", { name: "工单工作台", exact: true }),
  ).toBeVisible();
}
const record = {
  draft_id: "protocol-publication",
  revision: 1,
  status: "draft",
  eligibility: "draft",
  payload_sha256: "a".repeat(64),
  events: [],
  decisions: [],
  reports: [],
  payload: {
    retrospective_id: "protocol-source",
    candidate_id: "protocol-candidate",
    candidate_revision: 1,
    method: {
      title: '<img src=x onerror="window.publicationInjected=true">',
      product_version: "1.1",
      environment: "local_lab",
      mode: "startup",
      role: "planning_guidance_only",
    },
  },
};
test("REQ-1706: 发布审批绑定正文与报告、明确发布及撤销（协议）", async ({
  page,
}) => {
  let row: Record<string, unknown> = { ...record };
  await page.route("**/api/skill-drafts?*", (r) =>
    r.fulfill({ json: { items: [row], total: 1, offset: 0, limit: 10 } }),
  );
  await page.route("**/api/skill-drafts/protocol-publication", (r) =>
    r.fulfill({ json: row }),
  );
  const report = {
    report_id: "protocol-report",
    sha256: "b".repeat(64),
    report: {
      passed: true,
      checks: [{ name: "source_projection", passed: true }],
    },
  };
  await page.route(
    "**/api/skill-drafts/protocol-publication/regressions",
    (r) => {
      row = { ...row, reports: [report] };
      return r.fulfill({ json: report });
    },
  );
  await page.route(
    "**/api/skill-drafts/protocol-publication/decisions",
    (r) => {
      const body = r.request().postDataJSON();
      expect(body.report_id).toBe("protocol-report");
      expect(body.payload_sha256).toBe(record.payload_sha256);
      expect(body.revision).toBe(1);
      expect(body.decision).toBe("approve");
      row = { ...row, status: "approved", revision: 2 };
      return r.fulfill({ json: row });
    },
  );
  for (const kind of ["publish", "revoke"])
    await page.route(
      `**/api/skill-drafts/protocol-publication/${kind}`,
      (r) => {
        expect(r.request().postDataJSON().reason).toBe(
          "测试审批，不代表根因审定",
        );
        row = {
          ...row,
          status: kind === "publish" ? "published" : "revoked",
          revision: kind === "publish" ? 3 : 4,
        };
        return r.fulfill({ json: row });
      },
    );
  await login(page);
  await page.getByRole("button", { name: "经验与 Skill" }).click();
  await expect(
    page.getByRole("heading", { name: "候选 Skill 审批与发布" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "查看方法发布" }).click();
  await page.getByRole("button", { name: "回归与审批", exact: true }).click();
  await expect(page.getByRole("button", { name: "批准方法" })).toBeDisabled();
  await page.getByRole("button", { name: "回归与审批", exact: true }).click();
  await page.getByRole("button", { name: "运行固定回归" }).click();
  await page.getByLabel("方法审批与发布原因").fill("测试审批，不代表根因审定");
  await page.getByRole("button", { name: "批准方法" }).click();
  await expect(page.locator(".publication-detail")).toContainText("已批准");
  await page.getByRole("button", { name: "显式发布方法" }).click();
  await expect(page.locator(".publication-detail")).toContainText("已发布");
  await page.getByRole("button", { name: "撤销发布方法" }).click();
  await expect(page.locator(".publication-detail")).toContainText("已撤销");
  expect(
    await page.evaluate(
      () =>
        (window as unknown as { publicationInjected?: boolean })
          .publicationInjected,
    ),
  ).toBeUndefined();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(() =>
      Array.from(document.querySelectorAll("body *"))
        .filter((e) => e.getBoundingClientRect().right > innerWidth + 1)
        .slice(0, 12)
        .map(
          (e) =>
            `${e.tagName}.${e.className}: ${e.getBoundingClientRect().right}`,
        ),
    ),
  ).toEqual([]);
});

test("REQ-1706: 离开发布页面丢弃迟到详情（协议）", async ({ page }) => {
  await page.route("**/api/skill-drafts?*", (r) =>
    r.fulfill({ json: { items: [record], total: 1, offset: 0, limit: 10 } }),
  );
  let release!: () => void;
  await page.route("**/api/skill-drafts/protocol-publication", async (r) => {
    await new Promise<void>((resolve) => {
      release = resolve;
    });
    await r.fulfill({ json: record });
  });
  await login(page);
  await page.getByRole("button", { name: "经验与 Skill" }).click();
  await page.getByRole("button", { name: "查看方法发布" }).click();
  await expect.poll(() => !!release).toBe(true);
  await closeDetails(page);
  await page.getByRole("button", { name: "工单工作台" }).click();
  release();
  await expect(page.locator(".publication-detail")).toHaveCount(0);
});
