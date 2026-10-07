import { expect, test } from "vitest";
import { encodeDocumentFile } from "../src/document-file";

test("REQ-302: 上传编码逐字节保留中文与 PDF 字节", async () => {
  const bytes = new Uint8Array([0xe4, 0xb8, 0xad, 0, 0xff]);
  const result = await encodeDocumentFile(new File([bytes], "guide.pdf"));
  expect(result.format).toBe("pdf");
  expect(
    Array.from(atob(result.content_base64), (c) => c.charCodeAt(0)),
  ).toEqual(Array.from(bytes));
});

test("REQ-302: 不支持文件和超限原文在发送前明确拒绝", async () => {
  await expect(
    encodeDocumentFile(new File(["text"], "guide.exe")),
  ).rejects.toThrow("格式");
  await expect(
    encodeDocumentFile(new File([new Uint8Array(2097153)], "big.md")),
  ).rejects.toThrow("2 MiB");
});
