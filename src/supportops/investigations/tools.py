"""工具端再次约束白名单和范围，不依赖客户端或模型的自觉。"""

import json

from sqlalchemy import func, select

from supportops.api.errors import ServiceError
from supportops.auth.service import Principal, authentication_required
from supportops.chunks.chunking import digest
from supportops.db.models import AuthSession, User
from supportops.experiments.service import read_experiment
from supportops.investigations.contracts import ARGUMENTS
from supportops.retrieval import hybrid, service
from supportops.retrieval.contracts import HybridSearch
from supportops.tickets.service import read_ticket


def execute(session, scope, name, arguments):
    if name not in ARGUMENTS:
        raise ServiceError(403, "TOOL_NOT_ALLOWED", "工具不在本轮只读白名单。")
    payload = ARGUMENTS[name].model_validate(arguments)
    # 每次工具执行重新验证绑定会话，不能靠进程启动时的身份无限沿用权限。
    active = session.scalar(
        select(AuthSession.id)
        .join(User)
        .where(
            AuthSession.id == scope.session_id,
            AuthSession.user_id == scope.user_id,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > func.now(),
            User.active.is_(True),
            User.organization_id == scope.organization_id,
        )
    )
    if active is None:
        raise authentication_required()
    principal = Principal(scope.user_id, "mcp", scope.organization_id, "mcp", scope.session_id)
    ticket = read_ticket(session, principal, scope.ticket_id)
    if (
        ticket.product_version != scope.product_version
        or digest(ticket.model_dump(mode="json")) != scope.ticket_sha256
    ):
        raise ServiceError(409, "INVESTIGATION_SCOPE_CHANGED", "绑定工单已变化。")
    index = service.get_index(session, principal, scope.index_id)
    if index.corpus_sha256 != scope.corpus_sha256:
        raise ServiceError(409, "INVESTIGATION_SCOPE_CHANGED", "绑定知识快照已变化。")
    if name == "get_ticket":
        return {
            "ticket": ticket.model_dump(mode="json"),
            "evidence": [],
            "source_type": "ticket_statement",
        }
    if name == "search_knowledge":
        result = hybrid.search(
            session,
            principal,
            scope.index_id,
            HybridSearch(
                query=payload.query,
                product_version=scope.product_version,
                source_kinds=["document", "case"],
                top_k=3,
                candidate_limit=10,
                mode="bm25",
            ),
        )
        return {"evidence": result["items"], "mode": "bm25", "corpus_sha256": index.corpus_sha256}
    evidence = []
    if scope.experiment_id is not None:
        detail = read_experiment(session, principal, scope.experiment_id)
        if detail.product_version != scope.product_version:
            raise ServiceError(409, "INVESTIGATION_VERSION_MISMATCH", "实验版本与工单不一致。")
        for ordinal, observation in enumerate(detail.artifact.observations):
            if observation.phase != "failure":
                continue
            event = observation.model_dump(mode="json", exclude_none=True)
            evidence.append(
                {
                    "evidence_id": detail.evidence_ids[ordinal],
                    "kind": "log",
                    "product_version": scope.product_version,
                    "text": json.dumps(
                        event, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                    ),
                    "reference_url": f"/api/experiments/{scope.experiment_id}",
                    "source": {
                        "experiment_id": str(scope.experiment_id),
                        "run_id": str(detail.run_id),
                        "ordinal": ordinal,
                        "sha256": detail.sha256,
                        "event": event,
                    },
                    "text_verified": True,
                    "support_verified": False,
                }
            )
    return {
        "evidence": evidence,
        "source_type": "historical_lab_failure",
        "current_incident_verified": False,
    }
