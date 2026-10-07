"""本地与 CI 共用的零模型回归入口；原生检查失败即停止。"""

import os
import subprocess
import sys
from pathlib import Path

from supportops.models.provider import ModelSettings

ROOT = Path(__file__).resolve().parents[1]


def main():
    if ModelSettings().api_key is not None or any(
        os.environ.get(k) == "1"
        for k in ("SUPPORTOPS_LIVE_GUIDED", "SUPPORTOPS_LIVE_ANSWER_SAFETY")
    ):
        raise ValueError("零模型 CI 不允许模型凭据或真实付费开关。")
    for args in [
        ["-m", "pip", "check"],
        ["-m", "ruff", "check", "src", "tests", "scripts", "migrations"],
        ["-m", "ruff", "format", "--check", "src", "tests", "scripts", "migrations"],
        ["scripts/migrate.py", "--check"],
        ["scripts/migrate.py", "--test", "--check"],
        [
            "-m",
            "pytest",
            "-q",
            "--integration",
            "--offline-models",
            "--junitxml=local/backend-junit.xml",
        ],
        ["scripts/check-text.py"],
        ["scripts/check-public-secrets.py"],
    ]:
        subprocess.run([sys.executable, *args], cwd=ROOT, check=True)
    print("独立环境后端 CI 检查完成；真实 PostgreSQL；未启用付费模型。")


if __name__ == "__main__":
    main()
