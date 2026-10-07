import { closeDetails } from "./ui-navigation";
import { expect, test } from "@playwright/test";
import { readFileSync, writeFileSync } from "node:fs";
const folder = new URL(
  "../../docs/verification/phase-6-round-2/",
  import.meta.url,
);
const accounts = JSON.parse(
  readFileSync(
    new URL("../../local/demo-accounts.json", import.meta.url),
    "utf8",
  ),
).accounts as { username: string; password: string }[];

for (const file of [
  "live-configuration-attempt1.json",
  "live-pool-attempt1.json",
  "live-pool-attempt2.json",
]) {
  test(`REQ-1605/1606: 真实经验调查 ${file} 读回，零新增模型`, async ({
    page,
  }) => {
    const report = JSON.parse(readFileSync(new URL(file, folder), "utf8"));
    const record = report.response;
    let starts = 0;
    page.on("request", (r) => {
      if (
        r.method() === "POST" &&
        r.url().includes("hypothesis-investigations")
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
    const saved = page.waitForResponse(
      (r) =>
        r.request().method() === "GET" &&
        r.url().endsWith("/api/investigations/" + record.investigation_id),
    );
    await page
      .locator(".hypothesis-history")
      .filter({ hasText: record.investigation_id.slice(0, 12) })
      .getByRole("button")
      .click();
    expect(await (await saved).json()).toEqual(record);
    await expect(
      page.getByRole("heading", { name: "本次加载的经验快照" }),
    ).toBeVisible();
    const snapshot = page.locator(".investigation-skills");
    await snapshot.locator("summary").first().click();
    await expect(snapshot).toContainText(record.memory.loaded[0].body_sha256);
    await expect(snapshot).toContainText(record.memory.loaded[0].source_sha256);
    if (record.status === "failed")
      await expect(page.locator(".hypothesis-result")).toContainText(
        "MODEL_TIMEOUT",
      );
    if (file.includes("pool-attempt2"))
      await expect(page.locator(".hypothesis-result")).toContainText("未决");
    if (record.status === "completed") {
      await page
        .getByRole("button", { name: "回查调查原文", exact: true })
        .last()
        .click();
      await expect(page.locator(".hypothesis-reference")).toContainText(
        "text_verified",
      );
    }
    expect(starts).toBe(0);
    writeFileSync(
      new URL(
        `browser-${file}`,
        new URL(
          "../../docs/verification/ui-refinement-before-phase-7/",
          import.meta.url,
        ),
      ),
      JSON.stringify(
        {
          transport: "real_edge_http_after_restart",
          investigation_id: record.investigation_id,
          frozen_memory_unchanged: true,
          citation_checked: record.status === "completed",
          model_calls: 0,
        },
        null,
        2,
      ) + "\n",
    );
  });
}
