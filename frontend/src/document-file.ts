export async function encodeDocumentFile(file: File) {
  const format = file.name.split(".").pop()?.toLowerCase();
  if (!format || !["md", "pdf", "json"].includes(format))
    throw new Error("文件格式仅支持 md、pdf、json。");
  if (file.size > 2 * 1024 * 1024) throw new Error("原文超过 2 MiB。");
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  // 分段转换二进制，避免大文件 spread 超过 JavaScript 调用栈上限。
  for (let start = 0; start < bytes.length; start += 8192)
    binary += String.fromCharCode(...bytes.subarray(start, start + 8192));
  return { format, filename: file.name, content_base64: btoa(binary) };
}
