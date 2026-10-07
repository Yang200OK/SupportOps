import { expect, test } from "@playwright/test";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

test("REQ-2205: 全新库登录、持久化输入检查、隔离和私有凭据保护", async ({
  page,
  request,
}) => {
  const accountPath = process.env.SUPPORTOPS_REPRO_ACCOUNTS;
  const outputPath = process.env.SUPPORTOPS_REPRO_BROWSER_OUTPUT;
  if (!accountPath || !outputPath)
    throw new Error("必须显式指定私有凭据和本次证据目录。");
  const accounts = JSON.parse(readFileSync(accountPath, "utf8")).accounts;
  const account = accounts.find(
    (a: { username: string }) => a.username === "support_a",
  );
  const other = accounts.find(
    (a: { username: string }) => a.username === "support_b",
  );
  const errors: string[] = [];
  const posts: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("request", (r) => {
    if (r.method() === "POST") posts.push(new URL(r.url()).pathname);
  });
  await page.goto("/");
  await page.getByLabel("用户名", { exact: true }).fill(account.username);
  await page.getByLabel("密码", { exact: true }).fill(account.password);
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(page.getByText("数据库已就绪")).toBeVisible();
  const title = `独立复现构造工单 ${Date.now()}`;
  await page.getByRole("button", { name: "+ 新建工单" }).click();
  await page.getByLabel("问题标题", { exact: true }).fill(title);
  await page
    .getByLabel("问题描述", { exact: true })
    .fill("RD_TIMEOUT；缺版本，仅验证输入检查。");
  await page.getByRole("button", { name: "创建工单", exact: true }).click();
  const ticketId = await page.locator(".detail-card dd.mono").innerText();
  await page.getByRole("button", { name: "记录一次输入检查" }).click();
  await expect(page.locator(".run-detail .badge")).toHaveText("blocked");
  const runId = await page.locator(".run-meta .mono").first().innerText();
  const login = await request.post("/api/auth/login", {
    data: { username: other.username, password: other.password },
  });
  expect(login.ok()).toBe(true);
  const token = (await login.json()).access_token;
  const headers = { Authorization: `Bearer ${token}` };
  expect(
    (await request.get(`/api/tickets/${ticketId}`, { headers })).status(),
  ).toBe(404);
  expect((await request.get(`/api/runs/${runId}`, { headers })).status()).toBe(
    404,
  );
  await request.post("/api/auth/logout", { headers });
  // 凭据挂载在 Vite 根目录以外，不能通过文件接口暴露。
  const leak = await request.get(
    "/@fs/run/supportops-credentials/demo-accounts.json",
  );
  expect([403, 404]).toContain(leak.status());
  expect(
    posts.every(
      (p) =>
        p === "/api/auth/login" || p === "/api/tickets" || /\/runs$/.test(p),
    ),
  ).toBe(true);
  await page.reload();
  await expect(
    page.locator(".ticket-title").filter({ hasText: title }),
  ).toBeVisible();
  mkdirSync(outputPath, { recursive: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: resolve(outputPath, "reproduction-mobile.png"),
    animations: "disabled",
  });
  expect(errors).toEqual([]);
  writeFileSync(
    resolve(outputPath, "checks.json"),
    JSON.stringify(
      {
        ticket_id: ticketId,
        run_id: runId,
        checks: [
          "login",
          "blocked_run",
          "cross_org_404",
          "reload",
          "private_credentials_inaccessible",
          "mobile_no_overflow",
        ],
        model_calls: 0,
        page_errors: errors,
      },
      null,
      2,
    ) + "\n",
    "utf8",
  );
});
