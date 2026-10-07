"""只允许升级或核对明确的 SupportOps 数据库，不自动降级。"""

import argparse
import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from dotenv import dotenv_values
from sqlalchemy.engine import make_url


def main() -> None:
    parser = argparse.ArgumentParser(description="SupportOps 显式迁移")
    parser.add_argument("--test", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    values = dotenv_values(root / ".env")
    key = "SUPPORTOPS_TEST_ADMIN_DATABASE_URL" if args.test else "SUPPORTOPS_ADMIN_DATABASE_URL"
    raw = os.environ.get(key) or values.get(key)
    if not raw:
        raise RuntimeError("缺少本项目迁移管理员配置。")
    url = make_url(raw)
    expected = "supportops_test" if args.test else "supportops"
    if url.get_backend_name() != "postgresql" or url.database != expected:
        raise RuntimeError("迁移只允许显式指定的 SupportOps PostgreSQL 数据库。")
    os.environ["SUPPORTOPS_MIGRATION_DATABASE_URL"] = raw
    config = Config(str(root / "alembic.ini"))
    if args.check:
        command.check(config)
    else:
        command.upgrade(config, "head")
    print(f"迁移核对完成：{expected}。")


if __name__ == "__main__":
    main()
