"""首次生成本项目独立数据库凭据，拒绝覆盖既有配置。"""

import secrets
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    path = root / ".env"
    admin_password = secrets.token_hex(24)
    app_password = secrets.token_hex(24)
    lines = [
        f"SUPPORTOPS_POSTGRES_PASSWORD={admin_password}",
        f"SUPPORTOPS_APP_PASSWORD={app_password}",
    ]
    for prefix, database, role, password in [
        ("", "supportops", "supportops_app", app_password),
        ("ADMIN_", "supportops", "supportops_admin", admin_password),
        ("TEST_", "supportops_test", "supportops_app", app_password),
        ("TEST_ADMIN_", "supportops_test", "supportops_admin", admin_password),
    ]:
        lines.append(
            f"SUPPORTOPS_{prefix}DATABASE_URL="
            f"postgresql+psycopg://{role}:{password}@127.0.0.1:55432/{database}"
        )
    with path.open("x", encoding="utf-8", newline="\n") as file:
        file.write("\n".join(lines) + "\n")
    print("已生成独立 .env，未回显凭据；该文件禁止提交 GitHub。")


if __name__ == "__main__":
    main()
