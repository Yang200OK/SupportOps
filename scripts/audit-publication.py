"""核对实际候选或暂存字节，拒绝本机资料与敏感值；不会提交或上传。"""

import argparse
import hashlib
import json
import os
import re
import subprocess
from functools import cache
from pathlib import Path
from urllib.parse import unquote

from dotenv import dotenv_values
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_PARTS = {
    ".git",
    ".venv",
    "node_modules",
    "local",
    "outputs",
    "dist",
    "build",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "test-results",
    "playwright-report",
}
PRIVATE_FILES = {
    "AGENTS.md",
    "docs/implementation-plan.md",
    "docs/interview-overview.md",
    "docs/development-history.md",
    "docs/setup/github-first-upload.md",
}
KEY_PATTERN = re.compile(
    rb"(?:sk-[A-Za-z0-9_-]{20,}|LTAI[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{20,}|"
    rb"gh[pousr]_[A-Za-z0-9]{30,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)"
)


def validate_paths(names):
    for name in names:
        path = Path(name)
        if (
            path.is_absolute()
            or ".." in path.parts
            or PRIVATE_PARTS.intersection(path.parts)
            or name in PRIVATE_FILES
            or name.startswith("docs/specs/")
            or path.name.startswith(".env")
            and path.name != ".env.example"
        ):
            raise ValueError("文件超出公开范围：" + name)


def validate_links(root, names, read=None):
    available = set(names)
    for name in names:
        if Path(name).suffix != ".md":
            continue
        data = read(name) if read else (root / name).read_bytes()
        text = data.decode("utf-8-sig")
        for raw in re.findall(r"\[[^\]]*\]\(([^)]+)\)", text):
            target = raw.split(' "', 1)[0].strip("<>")
            if target.startswith(("https://", "http://", "mailto:", "#")):
                continue
            target = unquote(target.split("#", 1)[0])
            path = ((root / name).parent / target).resolve()
            if not path.is_relative_to(root.resolve()) or not path.is_file():
                raise ValueError("引用资源不存在或越界：" + name)
            if path.relative_to(root.resolve()).as_posix() not in available:
                raise ValueError("引用资源未在公开候选中：" + name)


def validate_content(root, names, secrets, read=None):
    for name in names:
        data = read(name) if read else (root / name).read_bytes()
        if any(value.encode("utf-8") in data for value in secrets if value) or KEY_PATTERN.search(
            data
        ):
            raise ValueError("发现敏感内容：" + name)
        if Path(name).suffix.lower() in {".png", ".pdf", ".jpg", ".jpeg"}:
            continue
        text = data.decode("utf-8-sig")
        if "\ufffd" in text or re.search(r"[A-Za-z]:\\+Users\\+[^\\\s]+", text):
            raise ValueError("发现编码或本机个人路径：" + name)


def validate_integrity(root, names, read=None):
    # 检查真正暂存的报告和图片，原证据不能被 Git 换行转换改变。
    available = set(names)

    def data(name):
        return read(name) if read else (root / name).read_bytes()

    entries = []
    reports = "docs/evidence/source-manifest.json"
    images = "docs/images/manifest.json"
    if reports in available:
        entries.extend(json.loads(data(reports))["report_integrity"])
    if images in available:
        entries.extend(
            {"path": "docs/images/" + item["file"], "sha256": item["sha256"]}
            for item in json.loads(data(images))["images"]
        )
    for item in entries:
        name = item["path"]
        if name not in available or hashlib.sha256(data(name)).hexdigest() != item["sha256"]:
            raise ValueError("原始文件摘要不一致：" + name)


def private_values(root, reproduction_names=()):
    values = set()
    values.update(
        os.environ[name]
        for name in ("DASHSCOPE_API_KEY", "SUPPORTOPS_MODEL_API_KEY")
        if os.environ.get(name)
    )
    paths = [root / ".env", root / ".env.lab"]
    for name in reproduction_names:
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", name):
            raise ValueError("独立复现名称不合法。")
        paths.append(root / "local/reproduction" / name / ".env")
    for path in paths:
        if not path.exists():
            continue
        # 无权读取时失败，不声称已扫描所有凭据。
        for name, value in dotenv_values(path).items():
            if not value:
                continue
            if any(key in name for key in ("PASSWORD", "API_KEY", "CONTROL_TOKEN")):
                values.add(value)
            if "DATABASE_URL" in name and make_url(value).password:
                values.add(make_url(value).password)
    for path in [
        root / "local/demo-accounts.json",
        *[root / "local/reproduction" / name / "demo-accounts.json" for name in reproduction_names],
    ]:
        if path.exists():
            values.update(
                a["password"] for a in json.loads(path.read_text(encoding="utf-8"))["accounts"]
            )
    return values


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--staged", action="store_true")
    parser.add_argument("--reproduction-name", action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / "local") or output.exists():
        raise ValueError("审计只写新的本地报告，不覆盖原尝试。")
    git = ["git", "-c", "safe.directory=" + ROOT.as_posix()]
    listing = (
        ["ls-files", "-z"]
        if args.staged
        else ["ls-files", "--cached", "--others", "--exclude-standard", "-z"]
    )
    names = sorted(
        set(
            x.decode("utf-8")
            for x in subprocess.check_output(git + listing, cwd=ROOT).split(b"\0")
            if x
        )
    )
    if not names:
        raise ValueError("没有可核对的公开候选。")
    validate_paths(names)

    # 同一文件只读取一次暂存字节，后续链接、摘要和凭据检查使用同一份内容。
    @cache
    def staged_bytes(name):
        return subprocess.check_output(git + ["show", ":" + name], cwd=ROOT)

    read = staged_bytes if args.staged else None
    validate_links(ROOT, names, read)
    validate_integrity(ROOT, names, read)
    validate_content(ROOT, names, private_values(ROOT, args.reproduction_name), read)
    result = {
        "mode": "staged" if args.staged else "candidate",
        "files": names,
        "count": len(names),
        "bytes": sum(len(read(n)) if read else (ROOT / n).stat().st_size for n in names),
        "credential_findings": [],
        "personal_path_findings": [],
        "local_private_files_excluded": True,
        "committed": False,
        "pushed": False,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {k: result[k] for k in ("mode", "count", "bytes", "committed", "pushed")},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
