import { closeDetails } from "./ui-navigation";
import { expect, test } from "@playwright/test";
import { readFileSync, writeFileSync } from "node:fs";

const folder = new URL(
  "../../docs/verification/phase-6-round-3/",
  import.meta.url,
);
const accounts = JSON.parse(
  readFileSync(
    new URL("../../local/demo-accounts.json", import.meta.url),
    "utf8",
  ),
).accounts as { username: string; password: string }[];

test("REQ-1706: 三批真实配对结果、持久化原调查与组织隔离，零新增模型", async ({
  page,
}) => {
  const account = accounts.find((a) => a.username === "support_a")!;
  let starts = 0;
  page.on("request", (r) => {
    if (r.method() === "POST" && r.url().includes("investigations")) starts++;
  });
  await page.goto("/");
  await page.getByLabel("用户名", { exact: true }).fill(account.username);
  await page.getByLabel("密码", { exact: true }).fill(account.password);
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(page.locator(".ticket-title").first()).toBeVisible();
  await page.getByRole("button", { name: "经验与 Skill" }).click();
  await page.getByRole("button", { name: "成对实验", exact: true }).click();
  const checked = [];
  for (const [cohort, file] of [
    ["max", "pairs-qwen-max-attempt1.json"],
    ["legacy", "pairs-attempt1.json"],
    ["current", "pairs-qwen-plus-0728-attempt1.json"],
  ]) {
    const frozen = JSON.parse(readFileSync(new URL(file, folder), "utf8"));
    expect(frozen.checks).toBe("passed");
    const pending = page.waitForResponse(
      (r) =>
        new URL(r.url()).pathname === "/api/memory-pairs" &&
        (new URL(r.url()).searchParams.get("cohort") ?? "current") === cohort,
    );
    await closeDetails(page);
    await page.getByLabel("实验批次").selectOption(cohort);
    const response = await pending;
    expect(response.status()).toBe(200);
    const body = await response.json();
    expect(body.complete).toBe(true);
    expect(body.model).toBe(frozen.model);
    expect(body.groups.off.attempted).toBe(4);
    expect(body.groups.on.attempted).toBe(4);
    expect(body.pairs).toHaveLength(4);
    expect(body.pairs.every((p: { comparable: boolean }) => p.comparable)).toBe(
      true,
    );
    await expect(page.locator(".memory-pairs-panel")).toContainText(
      `${frozen.model} ·`,
    );
    const first = frozen.attempts[0].response;
    const readback = page.waitForResponse((r) =>
      r.url().endsWith(`/api/investigations/${first.investigation_id}`),
    );
    await page.getByRole("button", { name: "查看配对明细" }).click();
    await page.getByRole("button", { name: "查看成对调查" }).first().click();
    expect(await (await readback).json()).toEqual(first);
    await expect(page.locator(".pair-investigation")).toContainText(
      first.stop_reason,
    );
    await closeDetails(page);
    checked.push({
      cohort,
      model: body.model,
      attempts: 8,
      original_unchanged: true,
      groups: body.groups,
    });
  }
  await page.locator(".memory-pairs-panel").scrollIntoViewIfNeeded();
  await page.screenshot({
    path: new URL(
      "regression-memory-pairs-live-pairs-desktop.png",
      new URL(
        "../../docs/verification/ui-refinement-before-phase-7/",
        import.meta.url,
      ),
    ).pathname.replace(/^\//, ""),
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: new URL(
      "regression-memory-pairs-live-pairs-mobile.png",
      new URL(
        "../../docs/verification/ui-refinement-before-phase-7/",
        import.meta.url,
      ),
    ).pathname.replace(/^\//, ""),
  });
  const other = accounts.find((a) => a.username === "support_b")!;
  const login = await page.request.post(
    "http://127.0.0.1:8010/api/auth/login",
    { data: { username: other.username, password: other.password } },
  );
  expect(login.status()).toBe(200);
  const headers = {
    Authorization: `Bearer ${(await login.json()).access_token}`,
  };
  const forbidden = await page.request.get(
    "http://127.0.0.1:8010/api/memory-pairs",
    { headers },
  );
  expect(forbidden.status()).toBe(404);
  await page.request.post("http://127.0.0.1:8010/api/auth/logout", { headers });
  expect(starts).toBe(0);
  writeFileSync(
    new URL(
      "browser-pairs-readback.json",
      new URL(
        "../../docs/verification/ui-refinement-before-phase-7/",
        import.meta.url,
      ),
    ),
    JSON.stringify(
      {
        transport: "real_edge_http_postgres",
        checked,
        cross_organization_status: 404,
        model_calls: starts,
      },
      null,
      2,
    ) + "\n",
    "utf8",
  );
});
