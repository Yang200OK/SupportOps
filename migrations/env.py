"""迁移 URL 只从显式环境变量读取，不写入日志或配置文件。"""

import os

from alembic import context
from sqlalchemy import create_engine, pool

from supportops.actions import models as action_models  # noqa: F401
from supportops.chunks import models as chunk_models  # noqa: F401
from supportops.coordination import execution_models as execution_models  # noqa: F401
from supportops.coordination import models as coordination_models  # noqa: F401
from supportops.db.models import Base
from supportops.documents import models as document_models  # noqa: F401
from supportops.experiments import models as experiment_models  # noqa: F401
from supportops.investigations import live_models as live_models  # noqa: F401
from supportops.investigations import models as investigation_models  # noqa: F401
from supportops.memory import models as memory_models  # noqa: F401
from supportops.retrieval import models as retrieval_models  # noqa: F401
from supportops.skills import publication_models as publication_models  # noqa: F401

url = os.environ.get("SUPPORTOPS_MIGRATION_DATABASE_URL")
if not url:
    raise RuntimeError("缺少显式的 SUPPORTOPS_MIGRATION_DATABASE_URL。")

if context.is_offline_mode():
    context.configure(
        url=url,
        target_metadata=Base.metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(
        url, poolclass=pool.NullPool, hide_parameters=True, connect_args={"connect_timeout": 5}
    )
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()
