import { expect, type Page } from "@playwright/test";

// 回归用例沿用原业务断言，只按新的弹窗入口操作。
export async function closeDetails(page: Page) {
  while (await page.locator(".detail-dialog:visible").count()) {
    const dialog = page.locator(".detail-dialog:visible").last();
    const title = (
      await dialog.locator(".el-dialog__title").innerText()
    ).trim();
    await dialog.getByRole("button", { name: "返回列表", exact: true }).click();
    await expect(
      page.getByRole("dialog", { name: title, exact: true }),
    ).not.toBeVisible();
  }
}

export async function openImport(page: Page) {
  await expect(
    page.locator(".documents-panel .list-tools > button"),
  ).toBeEnabled();
  await closeDetails(page);
  await page.getByRole("button", { name: "导入资料", exact: true }).click();
  await expect(
    page.getByRole("dialog", { name: "导入资料", exact: true }),
  ).toBeVisible();
}
