"""只在独立容器内初始化 / 启动，失败停止，不覆盖演示凭据。"""

import json
import os
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]


def main():
    values = dotenv_values(ROOT / ".env")
    for key in ("SUPPORTOPS_ADMIN_DATABASE_URL", "SUPPORTOPS_TEST_ADMIN_DATABASE_URL"):
        if make_url(values[key]).host != "postgres":
            raise ValueError("独立初始化只允许本 Compose 的 postgres 服务。")
    proof_path = ROOT / "local/initialization.json"
    admin = create_engine(values["SUPPORTOPS_ADMIN_DATABASE_URL"], hide_parameters=True)
    with admin.connect() as connection:
        tables = connection.scalar(text("SELECT count(*) FROM pg_tables WHERE schemaname='public'"))
        if not proof_path.exists() and tables:
            raise ValueError("首次复现必须使用全新业务库，不能接入已有表。")
    for args in (
        ["scripts/migrate.py"],
        ["scripts/migrate.py", "--test"],
        ["scripts/init-demo.py"],
    ):
        subprocess.run([sys.executable, *args], cwd=ROOT, check=True)
    with admin.connect() as connection:
        role = connection.execute(
            text(
                "SELECT rolsuper,rolbypassrls,rolcreatedb,rolcreaterole "
                "FROM pg_roles WHERE rolname='supportops_app'"
            )
        ).one()
        assert tuple(role) == (False, False, False, False)
        revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
        organizations = connection.scalar(text("SELECT count(*) FROM organizations"))
        users = connection.scalar(text("SELECT count(*) FROM users"))
        assert revision == "0014_coordination_execution" and organizations == 2 and users == 3
    if not proof_path.exists():
        proof = {
            "fresh_business_tables_before_migration": tables,
            "revision": revision,
            "organizations": organizations,
            "users": users,
            "app_role_privileged": False,
            "model_calls": 0,
            "python": sys.version,
        }
        proof_path.write_text(
            json.dumps(proof, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    admin.dispose()
    os.execv(
        sys.executable,
        [
            sys.executable,
            "-m",
            "uvicorn",
            "supportops.api.app:app",
            "--app-dir",
            "src",
            "--host",
            "0.0.0.0",
            "--port",
            "8010",
        ],
    )


if __name__ == "__main__":
    main()
