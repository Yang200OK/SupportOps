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
test("REQ-1706: 发布方法默认关闭、显式发送且切换清空快照（页面协议）", async ({
  page,
}) => {
  const body = JSON.parse(
    readFileSync(
      new URL(
        "../../docs/verification/phase-6-round-1/live-configuration-attempt2.json",
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
  const toggle = page.getByLabel("参考组织内已发布方法");
  await expect(toggle).not.toBeChecked();
  await toggle.check();
  await page.getByRole("button", { name: "开始假设调查", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "本次加载的 Skill 快照" }),
  ).toBeVisible();
  expect(payload.use_published_skills).toBe(true);
  expect(payload.use_skills).toBeUndefined();
  await toggle.uncheck();
  await expect(
    page.getByRole("heading", { name: "本次加载的 Skill 快照" }),
  ).toHaveCount(0);
});
