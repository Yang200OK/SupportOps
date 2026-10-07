import { closeDetails } from "./ui-navigation";
import { expect, test, type Page } from "@playwright/test";
import { readFileSync, writeFileSync } from "node:fs";

const accounts = JSON.parse(
  readFileSync(
    new URL("../../local/demo-accounts.json", import.meta.url),
    "utf8",
  ),
).accounts as { username: string; password: string }[];
const folder = new URL(
  "../../docs/verification/phase-6-round-1/",
  import.meta.url,
);
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

test("REQ-1506: 真实目录、范围与按需正文，桌面与手机", async ({ page }) => {
  const bodies: string[] = [];
  page.on("request", (request) => {
    if (
      request.url().includes("/api/skills/") &&
      request.url().includes("/versions/")
    )
      bodies.push(request.url());
  });
  await login(page);
  await page.getByRole("button", { name: "经验与 Skill" }).click();
  await page.getByRole("button", { name: "项目 Skill", exact: true }).click();
  await expect(page.locator(".skill-card")).toHaveCount(4);
  expect(bodies).toHaveLength(0);
  await page.getByLabel("Skill 运行模式").selectOption("startup");
  await expect(
    page.getByRole("button", { name: "读取适用方法", exact: true }),
  ).toHaveCount(1);
  await page.getByLabel("Skill 产品版本").selectOption("2.0");
  await page.getByRole("button", { name: "读取适用方法", exact: true }).click();
  await expect(page.locator(".skill-detail")).toContainText(
    "data/relaydesk/2.0/configuration.md",
  );
  await expect(page.locator(".skill-detail")).toContainText(
    "正文与来源核对通过",
  );
  expect(bodies).toHaveLength(1);
  await page.screenshot({
    path: new URL(
      "regression-skills-skills-desktop.png",
      new URL(
        "../../docs/verification/ui-refinement-before-phase-7/",
        import.meta.url,
      ),
    ).pathname.replace(/^\//, ""),
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator(".skill-detail").scrollIntoViewIfNeeded();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: new URL(
      "regression-skills-skills-mobile.png",
      new URL(
        "../../docs/verification/ui-refinement-before-phase-7/",
        import.meta.url,
      ),
    ).pathname.replace(/^\//, ""),
  });
  await closeDetails(page);
  await page.getByLabel("Skill 运行模式").selectOption("online");
  await expect(page.locator(".skill-detail")).toHaveCount(0);
  writeFileSync(
    new URL(
      "browser-catalog.json",
      new URL(
        "../../docs/verification/ui-refinement-before-phase-7/",
        import.meta.url,
      ),
    ),
    JSON.stringify(
      {
        transport: "real_edge_http",
        metadata_only_on_list: true,
        body_requests: bodies.length,
        selected_version: "2.0",
        mobile_no_overflow: true,
        model_calls: 0,
      },
      null,
      2,
    ) + "\n",
  );
});

test("REQ-1506: 页面协议拒绝迟到范围并转义正文", async ({ page }) => {
  await login(page);
  await page.getByRole("button", { name: "经验与 Skill" }).click();
  await page.getByRole("button", { name: "项目 Skill", exact: true }).click();
  await expect(page.locator(".skill-card")).toHaveCount(4);
  let release!: () => void;
  await page.route(
    "**/api/skills/pool-investigation/versions/**",
    async (route) => {
      await new Promise<void>((resolve) => {
        release = resolve;
      });
      await route.fulfill({
        json: {
          skill_id: "pool-investigation",
          version: "1.0.0",
          title: "页面协议",
          body: '<img src=x onerror="window.skillInjected=true">',
          sources: [],
          selected_product_version: "1.1",
          selected_mode: "online",
        },
      });
    },
  );
  await page
    .locator(".skill-card")
    .filter({ hasText: "连接池等待排查" })
    .getByRole("button")
    .click();
  await expect.poll(() => Boolean(release)).toBe(true);
  await page.getByLabel("Skill 产品版本").selectOption("2.0");
  release();
  await expect(
    page.getByRole("button", { name: "读取适用方法", exact: true }).first(),
  ).toBeEnabled();
  await expect(page.locator(".skill-detail")).toHaveCount(0);
  await page.unroute("**/api/skills/pool-investigation/versions/**");
  await page.route("**/api/skills/pool-investigation/versions/**", (route) =>
    route.fulfill({
      json: {
        skill_id: "pool-investigation",
        version: "1.0.0",
        title: "页面协议",
        body: '<img src=x onerror="window.skillInjected=true">',
        sources: [],
        selected_product_version: "2.0",
        selected_mode: "online",
      },
    }),
  );
  await page
    .locator(".skill-card")
    .filter({ hasText: "连接池等待排查" })
    .getByRole("button")
    .click();
  await expect(page.locator(".skill-detail pre")).toContainText("<img src=x");
  expect(
    await page.evaluate(
      () => (window as unknown as { skillInjected?: boolean }).skillInjected,
    ),
  ).toBeUndefined();
  await closeDetails(page);
  await page.getByRole("button", { name: "工单工作台" }).click();
  await expect(page.locator(".skill-detail")).toHaveCount(0);
});

test("REQ-1506: 调查开关与冻结方法快照（页面协议，零模型）", async ({
  page,
}) => {
  const record = JSON.parse(
    readFileSync(new URL("skill-snapshot.json", folder), "utf8"),
  ).response;
  await page.route("**/api/investigation-lab-runs?*", (route) =>
    route.fulfill({
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
  let payload: { use_skills?: boolean } = {};
  await page.route("**/api/tickets/*/hypothesis-investigations", (route) => {
    payload = route.request().postDataJSON();
    return route.fulfill({ status: 201, json: record });
  });
  await login(page);
  await page.getByRole("button", { name: "+ 新建工单" }).click();
  await page.getByLabel("问题标题", { exact: true }).fill("Skill 调查开关协议");
  await page
    .getByLabel("问题描述", { exact: true })
    .fill("RelayDesk 1.1 启动失败 RD_CONFIG_INVALID");
  await page.locator(".ticket-form select").first().selectOption("1.1");
  await page.getByRole("button", { name: "创建工单", exact: true }).click();
  await page
    .getByRole("dialog", { name: "工单详情", exact: true })
    .getByRole("button", { name: "假设调查", exact: true })
    .click();
  await page.getByLabel("本次实验运行").selectOption("protocol-startup");
  const toggle = page.getByLabel("使用适用 Skill 辅助调查");
  await expect(toggle).not.toBeChecked();
  await toggle.check();
  await page.getByRole("button", { name: "开始假设调查", exact: true }).click();
  await expect(page.locator(".investigation-skills")).toContainText(
    "配置与版本排查",
  );
  expect(payload.use_skills).toBe(true);
  await page.locator(".investigation-skills summary").click();
  await expect(page.locator(".investigation-skills pre")).toHaveText(
    record.skills.loaded[0].body,
  );
  await expect(page.locator(".investigation-skills")).toContainText(
    record.skills.loaded[0].body_sha256,
  );
  await toggle.uncheck();
  await expect(page.locator(".investigation-skills")).toHaveCount(0);
});

test("REQ-1505/1506: 真实历史快照读取（零模型）", async ({ page }) => {
  const record = JSON.parse(
    readFileSync(new URL("skill-snapshot.json", folder), "utf8"),
  ).response;
  let starts = 0;
  page.on("request", (request) => {
    if (request.method() === "POST" && request.url().includes("investigations"))
      starts++;
  });
  await login(page);
  // 登录后标题先出现，列表异步返回；先等首屏列表再决定是否翻页。
  await expect(page.locator(".ticket-title").first()).toBeVisible();
  let row = page.locator(".table-scroll tbody tr").filter({
    has: page.getByText(record.input_snapshot.ticket.title, { exact: true }),
  });
  for (let i = 0; i < 40 && !(await row.count()); i++) {
    const next = page
      .getByRole("button", { name: "下一页", exact: true })
      .first();
    const pending = page.waitForResponse(
      (r) =>
        r.request().method() === "GET" &&
        new URL(r.url()).pathname === "/api/tickets",
    );
    await next.click();
    await pending;
    await expect(page.locator(".loading")).toHaveCount(0);
    row = page.locator(".table-scroll tbody tr").filter({
      has: page.getByText(record.input_snapshot.ticket.title, {
        exact: true,
      }),
    });
  }
  await row.getByRole("button", { name: "查看", exact: true }).click();
  await page
    .getByRole("dialog", { name: "工单详情", exact: true })
    .getByRole("button", { name: "假设调查", exact: true })
    .click();
  const history = page
    .locator(".hypothesis-history")
    .filter({ hasText: record.investigation_id.slice(0, 12) });
  const response = page.waitForResponse(
    (r) =>
      r.request().method() === "GET" &&
      r.url().endsWith("/api/investigations/" + record.investigation_id),
  );
  await history
    .getByRole("button", { name: "读取历史假设调查", exact: true })
    .click();
  expect(await (await response).json()).toEqual(record);
  await expect(page.locator(".investigation-skills")).toContainText(
    record.skills.loaded[0].body_sha256,
  );
  expect(starts).toBe(0);
  writeFileSync(
    new URL(
      "browser-snapshot-readback.json",
      new URL(
        "../../docs/verification/ui-refinement-before-phase-7/",
        import.meta.url,
      ),
    ),
    JSON.stringify(
      {
        transport: "real_edge_http",
        investigation_id: record.investigation_id,
        frozen_skill_snapshot_unchanged: true,
        model_post_requests: starts,
      },
      null,
      2,
    ) + "\n",
  );
});
