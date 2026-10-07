"""只报告命中的公开文件路径，不打印或序列化本机凭据。"""

import json
import os
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {".git", ".venv", "node_modules", "dist", "local", "outputs", "__pycache__"}


def main() -> None:
    config = dotenv_values(ROOT / ".env")
    # 实验凭据与业务凭据隔离，检查公开文件时仍须覆盖两者。
    if (ROOT / ".env.lab").exists():
        config.update(dotenv_values(ROOT / ".env.lab"))
    values = set()
    for name, value in config.items():
        if value and ("PASSWORD" in name or "API_KEY" in name or "CONTROL_TOKEN" in name):
            values.add(value)
        if value and "DATABASE_URL" in name:
            password = make_url(value).password
            if password:
                values.add(password)
    accounts = ROOT / "local/demo-accounts.json"
    if accounts.exists():
        for account in json.loads(accounts.read_text(encoding="utf-8"))["accounts"]:
            values.add(account["password"])
    for name in ("DASHSCOPE_API_KEY", "SUPPORTOPS_MODEL_API_KEY"):
        if os.environ.get(name):
            values.add(os.environ[name])
    leaks = []
    inspected = 0
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if not path.is_file() or EXCLUDED.intersection(relative.parts):
            continue
        if path.name.startswith(".env") and path.name != ".env.example":
            continue
        if path.suffix in (".png", ".pyc"):
            continue
        content = path.read_bytes()
        inspected += 1
        if any(value.encode("utf-8") in content for value in values if value):
            leaks.append(str(relative))
    if leaks:
        raise RuntimeError("发现本机凭据的公开文件路径：" + ", ".join(leaks))
    print(f"公开文件凭据检查通过：{inspected} 个文件；没有输出凭据。")


if __name__ == "__main__":
    main()
