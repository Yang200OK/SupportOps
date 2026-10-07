"""检查项目维护文本的 UTF-8、已知乱码和不允许的字符。"""

import codecs
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP = {
    ".venv",
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "outputs",
    "local",
    "node_modules",
    "dist",
}
SUFFIXES = {
    ".py",
    ".md",
    ".json",
    ".jsonl",
    ".toml",
    ".ps1",
    ".txt",
    ".lock",
    ".sh",
    ".yaml",
    ".ini",
    ".mako",
    ".ts",
    ".vue",
    ".css",
    ".html",
}
EMOJI = re.compile(r"[\U0001f300-\U0001faff\u2600-\u27bf]")


def main() -> None:
    count = 0
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if not path.is_file() or SKIP.intersection(relative.parts):
            continue
        if path.suffix not in SUFFIXES:
            continue
        content = path.read_bytes()
        # Windows PowerShell 5.1 依靠 BOM 识别 UTF-8，避免中文被按 ANSI 读取。
        if path.suffix == ".ps1" and not content.startswith(codecs.BOM_UTF8):
            raise ValueError(f"PowerShell 脚本缺少 UTF-8 BOM：{relative}")
        text = content.decode("utf-8-sig", errors="strict")
        # 使用转义字符声明异常标记，检查脚本本身不会包含真实异常文本。
        if "\ufffd" in text or "\u951f\u65a4\u62f7" in text or EMOJI.search(text):
            raise ValueError(f"发现异常字符：{relative}")
        count += 1
    print(f"UTF-8 和字符检查通过：{count} 个文本文件。")


if __name__ == "__main__":
    main()
