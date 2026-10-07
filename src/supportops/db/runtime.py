"""请求事务和数据库角色 / 迁移就绪检查。"""

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import create_engine, text
from sqlalchemy.exc import InterfaceError, OperationalError, ProgrammingError
from sqlalchemy.orm import Session

from supportops.api.errors import ServiceError

MIGRATION = "0014_coordination_execution"
DATABASE_FAILURE = "数据库未配置、不可用或角色 / 迁移不符合要求。"


def unavailable() -> ServiceError:
    return ServiceError(503, "DATABASE_UNAVAILABLE", DATABASE_FAILURE)


class Database:
    def __init__(self, url: str):
        self.engine = create_engine(
            url,
            pool_size=5,
            max_overflow=0,
            pool_timeout=5,
            hide_parameters=True,
            connect_args={"connect_timeout": 3},
        )

    def check_ready(self, session: Session) -> None:
        role = (
            session.execute(
                text(
                    "SELECT current_user AS name, current_database() AS database, "
                    "rolsuper, rolbypassrls, rolcreatedb, rolcreaterole "
                    "FROM pg_roles WHERE rolname=current_user"
                )
            )
            .mappings()
            .one()
        )
        if role["name"] != "supportops_app" or any(
            role[key] for key in ("rolsuper", "rolbypassrls", "rolcreatedb", "rolcreaterole")
        ):
            raise unavailable()
        if role["database"] not in ("supportops", "supportops_test"):
            raise unavailable()
        if session.execute(text("SELECT version_num FROM alembic_version")).scalars().all() != [
            MIGRATION
        ]:
            raise unavailable()
        for table in (
            "tickets",
            "runs",
            "documents",
            "document_revisions",
            "chunk_sets",
            "experiments",
            "retrieval_indexes",
            "retrieval_entries",
            "investigations",
            "investigation_lab_runs",
            "action_jobs",
            "action_approvals",
            "action_events",
            "retrospectives",
            "experience_candidates",
            "memory_events",
            "memory_conflicts",
            "skill_drafts",
            "skill_regressions",
            "skill_decisions",
            "skill_publication_events",
            "coordination_boards",
            "coordination_events",
            "coordination_executions",
            "coordination_execution_events",
        ):
            policy = (
                session.execute(
                    text(
                        "SELECT relrowsecurity, relforcerowsecurity, "
                        "pg_get_userbyid(relowner) AS owner "
                        "FROM pg_class JOIN pg_namespace ON relnamespace=pg_namespace.oid "
                        "WHERE nspname='public' AND relname=:table"
                    ),
                    {"table": table},
                )
                .mappings()
                .one_or_none()
            )
            if not policy or not policy["relrowsecurity"] or not policy["relforcerowsecurity"]:
                raise unavailable()
            if policy["owner"] == role["name"]:
                raise unavailable()


def request_session(request: Request) -> Iterator[Session]:
    database = request.app.state.database
    if database is None:
        raise unavailable()
    try:
        with Session(database.engine) as session, session.begin():
            database.check_ready(session)
            yield session
    except (OperationalError, InterfaceError, ProgrammingError):
        # 公开错误不携带连接 URL、SQL、参数或底层异常正文。
        raise unavailable() from None
