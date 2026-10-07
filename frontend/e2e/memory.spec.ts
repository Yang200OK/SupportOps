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
  retrospective_id: "protocol-one",
  investigation_id: "protocol-investigation",
  ticket_id: "protocol-ticket",
  source_sha256: "a".repeat(64),
  summary_sha256: "b".repeat(64),
  model_calls: 0,
  summary: {
    status: "completed",
    event_count: 12,
    plan_count: 3,
    action: null,
    limitation: "待审核",
  },
  candidate: {
    candidate_id: "candidate-one",
    retrospective_id: "protocol-one",
    body_sha256: "c".repeat(64),
    status: "candidate",
    eligibility: "eligible_unreviewed",
    revision: 1,
    expires_at: "2099-01-01T00:00:00Z",
    events: [],
    conflicts: [],
    body: {
      title: '<img src=x onerror="window.memoryInjected=true">',
      symptoms: "配置拒绝",
      product_version: "1.1",
      mode: "startup",
      environment: "local_lab",
      checks: [],
      source_evidence: [],
      limitation: "历史方法不代表现场证据",
    },
  },
};
test("REQ-1606: 经验页面协议、正文转义与明确撤销原因", async ({ page }) => {
  let status = "candidate";
  await page.route("**/api/retrospectives?*", (r) =>
    r.fulfill({
      json: {
        items: [{ ...record, candidate: { ...record.candidate, status } }],
        total: 1,
        offset: 0,
        limit: 10,
      },
    }),
  );
  await page.route("**/api/retrospectives/protocol-one?*", (r) =>
    r.fulfill({ json: { ...record, source: { events: ["原始事件"] } } }),
  );
  await page.route("**/api/experiences/candidate-one/decisions", async (r) => {
    expect(r.request().postDataJSON()).toEqual({
      revision: 1,
      decision: "revoke",
      reason: "错误经验需要撤销",
    });
    status = "revoked";
    await r.fulfill({
      json: {
        ...record.candidate,
        status,
        eligibility: "revoked",
        revision: 2,
      },
    });
  });
  await login(page);
  await page.getByRole("button", { name: "经验与 Skill" }).click();
  await page.getByRole("button", { name: "事件复盘", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "事件复盘与经验候选" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "查看复盘与来源" }).click();
  await page.getByRole("button", { name: "经验与治理", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "撤销经验", exact: true }),
  ).toBeDisabled();
  expect(
    await page.evaluate(
      () => (window as unknown as { memoryInjected?: boolean }).memoryInjected,
    ),
  ).toBeUndefined();
  await page.getByRole("button", { name: "经验与治理", exact: true }).click();
  await page.getByLabel("经验治理原因").fill("错误经验需要撤销");
  await page.getByRole("button", { name: "撤销经验", exact: true }).click();
  await expect(page.locator(".memory-detail")).toContainText("已撤销 · 修订 2");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});

test("REQ-1606: 离开经验页面后丢弃迟到详情", async ({ page }) => {
  await page.route("**/api/retrospectives?*", (r) =>
    r.fulfill({ json: { items: [record], total: 1, offset: 0, limit: 10 } }),
  );
  let release!: () => void;
  await page.route("**/api/retrospectives/protocol-one?*", async (r) => {
    await new Promise<void>((resolve) => {
      release = resolve;
    });
    await r.fulfill({ json: record });
  });
  await login(page);
  await page.getByRole("button", { name: "经验与 Skill" }).click();
  await page.getByRole("button", { name: "事件复盘", exact: true }).click();
  await page.getByRole("button", { name: "查看复盘与来源" }).click();
  await expect.poll(() => !!release).toBe(true);
  await closeDetails(page);
  await page.getByRole("button", { name: "工单工作台" }).click();
  release();
  await expect(page.locator(".memory-detail")).toHaveCount(0);
});

test("REQ-1606: 经验开关显式发送且范围变化清空快照（页面协议）", async ({
  page,
}) => {
  const body = JSON.parse(
    readFileSync(
      new URL(
        "../../docs/verification/phase-6-round-2/zero-model-attempt3.json",
        import.meta.url,
      ),
      "utf8",
    ),
  ).response;
  await page.route("**/api/investigation-lab-runs?*", (r) =>
    r.fulfill({
      json: {
        items: [
          {
            lab_run_id: "protocol-startup",
            product_version: "1.1",
            mode: "startup",
            expires_at: "2099-01-01T00:00:00Z",
          },
        ],
        total: 1,
        offset: 0,
        limit: 30,
      },
    }),
  );
  let payload: Record<string, unknown> = {};
  await page.route("**/api/tickets/*/hypothesis-investigations", async (r) => {
    if (r.request().method() !== "POST") return r.continue();
    payload = r.request().postDataJSON();
    await r.fulfill({ json: body });
  });
  await login(page);
  await page.getByRole("button", { name: "新建工单" }).click();
  await page.getByLabel("问题标题").fill("经验开关页面协议");
  await page
    .getByLabel("问题描述")
    .fill("RelayDesk 1.1 启动失败 RD_CONFIG_INVALID");
  await page.locator(".ticket-form select").first().selectOption("1.1");
  await page.getByRole("button", { name: "创建工单", exact: true }).click();
  await page
    .getByRole("dialog", { name: "工单详情", exact: true })
    .getByRole("button", { name: "假设调查", exact: true })
    .click();
  await page.getByLabel("本次实验运行").selectOption("protocol-startup");
  const toggle = page.getByLabel("参考可召回经验候选（待审核）");
  await expect(toggle).not.toBeChecked();
  await toggle.check();
  await page.getByRole("button", { name: "开始假设调查", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "本次加载的经验快照" }),
  ).toBeVisible();
  expect(payload.use_memory).toBe(true);
  expect(payload.use_skills).toBeUndefined();
  await toggle.uncheck();
  await expect(
    page.getByRole("heading", { name: "本次加载的经验快照" }),
  ).toHaveCount(0);
});
