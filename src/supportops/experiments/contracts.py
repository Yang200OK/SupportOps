"""摘要证明包未改变，不把上传声明当成服务器验证过的工具执行。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from supportops.lab.contracts import LabBundle, Strict, Version


class ImportExperiment(Strict):
    artifact: LabBundle
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ExperimentView(Strict):
    experiment_id: UUID
    run_id: UUID
    product_version: Version
    sha256: str
    created_at: datetime
    observation_count: int
    provenance: Literal["submitted_lab_observations"] = "submitted_lab_observations"
    execution_verified_by_api: Literal[False] = False
    reused: bool = False


class ExperimentDetail(ExperimentView):
    artifact: LabBundle
    evidence_ids: list[str]
    text_verified: Literal[True] = True


class ExperimentPage(Strict):
    items: list[ExperimentView]
    total: int
    offset: int
    limit: int
