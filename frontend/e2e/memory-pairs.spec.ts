import { closeDetails } from "./ui-navigation";
import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";

const accounts = JSON.parse(
  readFileSync(
    new URL("../../local/demo-accounts.json", import.meta.url),
    "utf8",
  ),
).accounts as { username: string; password: string }[];
test("REQ-1706: 成对结果保留失败分母并回查调查（协议）", async ({ page }) => {
  const account = accounts.find((a) => a.username === "support_a")!;
  await page.route("**/api/memory-pairs*", (r) =>
    r.fulfill({
      json: {
        available: true,
        model: r.request().url().includes("cohort=legacy")
          ? "qwen3.7-plus"
          : r.request().url().includes("cohort=max")
            ? "qwen-max"
            : "qwen-plus-2025-07-28",
        complete: true,
        groups: {
          off: {
            attempted: 4,
            completed: 3,
            failed_or_stopped: 1,
            missing_responses: 0,
            known_input_tokens: 100,
            known_output_tokens: 20,
            unknown_model_calls: 1,
          },
          on: {
            attempted: 4,
            completed: 2,
            failed_or_stopped: 2,
            missing_responses: 0,
            known_input_tokens: 150,
            known_output_tokens: 25,
            unknown_model_calls: 0,
          },
        },
        pairs: [],
        investigations: [
          {
            family: "configuration",
            use_memory: false,
            investigation_id: "protocol-pair-investigation",
          },
        ],
        limitation: "小样本，不代表人工准确率",
      },
    }),
  );
  await page.route("**/api/investigations/protocol-pair-investigation", (r) =>
    r.fulfill({
      json: {
        status: "failed",
        stop_reason: "MODEL_TIMEOUT",
        human_reviewed: false,
      },
    }),
  );
  await page.goto("/");
  await page.getByLabel("用户名", { exact: true }).fill(account.username);
  await page.getByLabel("密码", { exact: true }).fill(account.password);
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(page.locator(".ticket-title").first()).toBeVisible();
  await page.getByRole("button", { name: "经验与 Skill" }).click();
  await page.getByRole("button", { name: "成对实验", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "记忆成对实验" }),
  ).toBeVisible();
  await expect(page.locator(".memory-pairs-panel")).toContainText("3 / 4");
  await expect(page.locator(".memory-pairs-panel")).toContainText("2 / 4");
  await page.getByRole("button", { name: "查看配对明细" }).click();
  await page.getByRole("button", { name: "查看成对调查" }).click();
  await expect(page.locator(".pair-investigation")).toContainText(
    "MODEL_TIMEOUT",
  );
  await closeDetails(page);
  await page.getByLabel("实验批次").selectOption("max");
  await expect(page.locator(".memory-pairs-panel")).toContainText("qwen-max ·");
  await expect(page.locator(".pair-investigation")).toHaveCount(0);
  await closeDetails(page);
  await page.getByLabel("实验批次").selectOption("legacy");
  await expect(page.locator(".memory-pairs-panel")).toContainText(
    "qwen3.7-plus ·",
  );
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});
