"""建立两个本地演示组织与三个账号，不提供公开默认口令。"""

import json
import secrets
from pathlib import Path
from uuid import uuid4

from dotenv import dotenv_values
from pwdlib import PasswordHash
from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from supportops.db.models import Organization, User


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    path = root / "local/demo-accounts.json"
    values = dotenv_values(root / ".env")
    raw = values.get("SUPPORTOPS_ADMIN_DATABASE_URL")
    if not raw or make_url(raw).database != "supportops":
        raise RuntimeError("只允许初始化本项目正式演示数据库。")
    engine = create_engine(raw, hide_parameters=True, connect_args={"connect_timeout": 5})
    passwords = PasswordHash.recommended()
    try:
        if path.exists():
            saved = json.loads(path.read_text(encoding="utf-8"))
            with Session(engine) as session:
                for item in saved["accounts"]:
                    user = session.scalar(select(User).where(User.username == item["username"]))
                    if user is None or not passwords.verify(item["password"], user.password_hash):
                        raise RuntimeError("本地演示凭据与数据库不一致，不能自动重置既有账号。")
            print("演示账号已存在并核对通过，没有更改口令。")
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        groups = {
            "a": Organization(id=uuid4(), name="演示支持团队 A"),
            "b": Organization(id=uuid4(), name="演示支持团队 B"),
        }
        accounts = []
        with Session(engine) as session, session.begin():
            if session.scalar(
                select(User).where(User.username.in_(["support_a", "support_b", "support_a2"]))
            ):
                raise RuntimeError("演示用户名已经存在；缺少本地凭据时不能自动覆盖。")
            session.add_all(groups.values())
            session.flush()
            for name, group in (("support_a", "a"), ("support_b", "b"), ("support_a2", "a")):
                password = secrets.token_urlsafe(24)
                organization = groups[group]
                session.add(
                    User(
                        id=uuid4(),
                        organization_id=organization.id,
                        username=name,
                        password_hash=passwords.hash(password),
                    )
                )
                accounts.append(
                    {
                        "username": name,
                        "password": password,
                        "organization_id": str(organization.id),
                        "organization_name": organization.name,
                    }
                )
            session.flush()
        with path.open("x", encoding="utf-8") as file:
            json.dump(
                {"data_kind": "local_demo_credentials", "accounts": accounts},
                file,
                ensure_ascii=False,
                indent=2,
            )
            file.write("\n")
        print("演示账号已创建；凭据仅保存在 local/demo-accounts.json，禁止提交 GitHub。")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
