import { closeDetails } from "./ui-navigation";
import { expect, test } from "@playwright/test";
import { existsSync, readFileSync, writeFileSync } from "node:fs";

const folder = new URL(
  "../../docs/verification/phase-6-round-1/",
  import.meta.url,
);
const accounts = JSON.parse(
  readFileSync(
    new URL("../../local/demo-accounts.json", import.meta.url),
    "utf8",
  ),
).accounts as { username: string; password: string }[];

for (const [scenario, file] of [
  ["configuration", "live-configuration-attempt2.json"],
  ["pool", "live-pool-attempt1.json"],
]) {
  test(`REQ-1504/1506: 真实 ${scenario} 模型记录、方法与引文读回（零新增模型）`, async ({
    page,
  }) => {
    const report = new URL(file!, folder);
    test.skip(!existsSync(report), "需先显式运行固定场景真实验证");
    const record = JSON.parse(readFileSync(report, "utf8")).response;
    expect(record.status).toBe("completed");
    let starts = 0;
    page.on("request", (request) => {
      if (
        request.method() === "POST" &&
        request.url().includes("investigations")
      )
        starts++;
    });
    const account = accounts.find((a) => a.username === "support_a")!;
    await page.goto("/");
    await page.getByLabel("用户名", { exact: true }).fill(account.username);
    await page.getByLabel("密码", { exact: true }).fill(account.password);
    await page.getByRole("button", { name: "进入工作台" }).click();
    await expect(page.locator(".ticket-title").first()).toBeVisible();
    const row = page.locator(".table-scroll tbody tr").filter({
      has: page.getByText(record.input_snapshot.ticket.title, {
        exact: true,
      }),
    });
    for (let i = 0; i < 40 && !(await row.count()); i++) {
      const pending = page.waitForResponse(
        (r) =>
          r.request().method() === "GET" &&
          new URL(r.url()).pathname === "/api/tickets",
      );
      await page
        .getByRole("button", { name: "下一页", exact: true })
        .first()
        .click();
      await pending;
      await expect(page.locator(".loading")).toHaveCount(0);
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
    await page.locator(".investigation-skills summary").first().click();
    await expect(page.locator(".investigation-skills pre").first()).toHaveText(
      record.skills.loaded[0].body,
    );
    await expect(page.locator(".investigation-skills")).toContainText(
      record.skills.loaded[0].body_sha256,
    );
    await page
      .getByRole("button", { name: "回查调查原文", exact: true })
      .last()
      .click();
    await expect(page.locator(".hypothesis-reference")).toContainText(
      "text_verified",
    );
    await page.locator(".investigation-skills").scrollIntoViewIfNeeded();
    await page.screenshot({
      path: new URL(
        `skill-live-${scenario}-desktop.png`,
        folder,
      ).pathname.replace(/^\//, ""),
    });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator(".investigation-skills").scrollIntoViewIfNeeded();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: new URL(
        `skill-live-${scenario}-mobile.png`,
        folder,
      ).pathname.replace(/^\//, ""),
    });
    expect(starts).toBe(0);
    writeFileSync(
      new URL(
        `browser-live-${scenario}-readback.json`,
        new URL(
          "../../docs/verification/ui-refinement-before-phase-7/",
          import.meta.url,
        ),
      ),
      JSON.stringify(
        {
          transport: "real_edge_http_after_api_restart",
          investigation_id: record.investigation_id,
          response_unchanged: true,
          skill_body_unchanged: true,
          citation_checked: true,
          mobile_no_overflow: true,
          new_model_calls: starts,
        },
        null,
        2,
      ) + "\n",
    );
  });
}
