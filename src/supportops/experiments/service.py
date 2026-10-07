"""复用会话组织与请求事务，只导入和读回严格公共证据。"""

from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from supportops.api.errors import ServiceError
from supportops.experiments.contracts import ExperimentDetail, ExperimentPage, ExperimentView
from supportops.experiments.models import Experiment
from supportops.lab.contracts import LabBundle


def view(record, reused=False):
    return ExperimentView(
        experiment_id=record.id,
        run_id=record.run_id,
        product_version=record.product_version,
        sha256=record.sha256,
        created_at=record.created_at,
        observation_count=len(record.artifact["observations"]),
        reused=reused,
    )


def import_bundle(session, principal, payload):
    if payload.artifact.digest() != payload.sha256:
        raise ServiceError(422, "OBSERVATION_DIGEST_MISMATCH", "观测包 SHA-256 不一致。")
    identity = uuid4()
    inserted = session.scalar(
        insert(Experiment)
        .values(
            id=identity,
            organization_id=principal.organization_id,
            requester_id=principal.user_id,
            run_id=payload.artifact.run_id,
            product_version=payload.artifact.product_version,
            sha256=payload.sha256,
            artifact=payload.artifact.model_dump(mode="json", exclude_none=True),
        )
        .on_conflict_do_nothing(constraint="uq_experiments_run")
        .returning(Experiment.id)
    )
    record = session.scalar(
        select(Experiment).where(
            Experiment.organization_id == principal.organization_id,
            Experiment.run_id == payload.artifact.run_id,
        )
    )
    if record.sha256 != payload.sha256:
        raise ServiceError(409, "OBSERVATION_RUN_CONFLICT", "相同运行标识已经绑定另一份观测。")
    return view(record, reused=inserted is None)


def list_experiments(session, principal, offset, limit):
    scope = Experiment.organization_id == principal.organization_id
    records = session.scalars(
        select(Experiment)
        .where(scope)
        .order_by(Experiment.created_at.desc(), Experiment.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    total = session.scalar(select(func.count()).select_from(Experiment).where(scope))
    return ExperimentPage(items=[view(r) for r in records], total=total, offset=offset, limit=limit)


def read_experiment(session, principal, identity):
    record = session.scalar(
        select(Experiment).where(
            Experiment.id == identity, Experiment.organization_id == principal.organization_id
        )
    )
    if record is None:
        raise ServiceError(404, "EXPERIMENT_NOT_FOUND", "实验观测不存在或不属于当前组织。")
    try:
        artifact = LabBundle.model_validate(record.artifact)
    except ValidationError as exc:
        raise ServiceError(409, "OBSERVATION_INTEGRITY_FAILED", "保存的观测包结构不一致。") from exc
    if (
        artifact.digest() != record.sha256
        or artifact.run_id != record.run_id
        or artifact.product_version != record.product_version
    ):
        raise ServiceError(409, "OBSERVATION_INTEGRITY_FAILED", "保存的观测包摘要不一致。")
    return ExperimentDetail(
        **view(record).model_dump(), artifact=artifact, evidence_ids=artifact.evidence_ids()
    )
