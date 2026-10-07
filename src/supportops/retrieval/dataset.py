"""离线查询与相关性标签独立存放，普通检索只接受 Search。"""

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from supportops.evaluations.contracts import Digest, Identifier
from supportops.lab.contracts import Strict
from supportops.retrieval.contracts import Search


class RetrievalTask(Strict):
    task_id: Identifier
    source_group: Identifier
    split: Literal["dev", "holdout"]
    request: Search


class RetrievalDataset(Strict):
    schema_version: Literal["supportops.retrieval-dataset.v1"]
    index_id: UUID
    corpus_sha256: Digest
    tasks: list[RetrievalTask] = Field(min_length=1, max_length=120)

    @model_validator(mode="after")
    def partitioned(self):
        if len({t.task_id for t in self.tasks}) != len(self.tasks):
            raise ValueError("检索任务身份必须唯一。")
        groups = {}
        for task in self.tasks:
            if task.source_group in groups and groups[task.source_group] != task.split:
                raise ValueError("同家族检索问题不能跨分区。")
            groups[task.source_group] = task.split
        return self


class Label(Strict):
    task_id: Identifier
    relevance: dict[str, int]
    annotation_origin: Literal["section_rule_draft", "controlled_experiment_draft"]
    human_reviewed: Literal[False] = False

    @model_validator(mode="after")
    def grades(self):
        if not self.relevance or any(
            type(g) is not int or not 1 <= g <= 3 for g in self.relevance.values()
        ):
            raise ValueError("相关性初稿必须有 1 至 3 级的证据锚点。")
        return self


class Qrels(Strict):
    schema_version: Literal["supportops.retrieval-qrels.v1"]
    dataset_sha256: Digest
    labels: list[Label] = Field(min_length=1, max_length=120)
