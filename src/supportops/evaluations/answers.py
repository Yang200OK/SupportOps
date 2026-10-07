"""离线规则评测不参与推理，不能代替人工事实准确率。"""

import math
import re
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from supportops.chunks.chunking import digest
from supportops.evaluations.contracts import Contract, Digest, Identifier
from supportops.rag.contracts import AnswerRequest
from supportops.retrieval.contexts import text_hash


class Task(Contract):
    task_id: Identifier
    source_group: Identifier
    split: Literal["dev", "holdout"]
    category: Literal["fact", "injection", "wrong_version", "no_evidence"]
    request: AnswerRequest


class Dataset(Contract):
    schema_version: Literal["supportops.answer-dataset.v1"]
    index_id: UUID
    corpus_sha256: Digest
    tasks: list[Task] = Field(min_length=1, max_length=120)

    @model_validator(mode="after")
    def partitioned(self):
        groups = {}
        if len({t.task_id for t in self.tasks}) != len(self.tasks):
            raise ValueError("任务身份必须唯一。")
        for task in self.tasks:
            if task.source_group in groups and groups[task.source_group] != task.split:
                raise ValueError("同来源 / 模板家族不能跨分区。")
            groups[task.source_group] = task.split
        return self


class Label(Contract):
    task_id: Identifier
    expected_status: Literal["reviewed", "no_evidence", "insufficient_evidence"]
    facts: list[list[str]] = Field(max_length=12)
    forbidden_patterns: list[str] = Field(max_length=12)
    required_evidence_ids: list[Identifier] = Field(max_length=12)
    annotation_origin: Literal["source_rule_draft"] = "source_rule_draft"
    human_reviewed: Literal[False] = False

    @model_validator(mode="after")
    def rules(self):
        if any(not fact or len(fact) > 5 for fact in self.facts):
            raise ValueError("每个原子事实需要一至五个同结论匹配规则。")
        for pattern in [p for fact in self.facts for p in fact] + self.forbidden_patterns:
            if not pattern or len(pattern) > 160:
                raise ValueError("离线匹配规则超出预算。")
            re.compile(pattern)
        return self


class Labels(Contract):
    schema_version: Literal["supportops.answer-labels.v1"]
    dataset_sha256: Digest
    labels: list[Label] = Field(min_length=1, max_length=120)

    def bind(self, dataset):
        ids = [item.task_id for item in self.labels]
        if self.dataset_sha256 != digest(dataset.model_dump(mode="json")) or (
            len(set(ids)) != len(ids) or set(ids) != {t.task_id for t in dataset.tasks}
        ):
            raise ValueError("标签必须哈希绑定全部唯一任务。")


def score(body, request, label):
    # 只匹配同一结论正文，引文与其他结论不能补足一个原子事实。
    claims = body["claims"]
    facts = label["facts"]
    matched = sum(
        any(all(re.search(p, c["text"], re.I) for p in fact) for c in claims) for fact in facts
    )
    windows = {c["context_id"]: c for c in body["contexts"]}
    hits = {h["evidence_id"]: h for h in body["retrieval"]["items"]}
    literal = invalid = uncited = 0
    cited = set()
    for claim in claims:
        if not claim["citations"]:
            uncited += 1
        for ref in claim["citations"]:
            window = windows.get(ref["context_id"])
            valid = (
                window is not None
                and ref["evidence_id"] in hits
                and ref["evidence_id"] in window["anchor_evidence_ids"]
                and type(ref["start"]) is int
                and type(ref["end"]) is int
                and 0 <= ref["start"] < ref["end"] <= len(window["text"])
                and window["text"][ref["start"] : ref["end"]] == ref["quote"]
                and text_hash(window["text"]) == ref["context_text_sha256"]
            )
            literal += int(valid)
            invalid += int(not valid)
            if valid:
                cited.add(ref["evidence_id"])
    return {
        "outcome_match": body["status"] == label["expected_status"],
        "rule_fact_coverage": matched / len(facts) if facts else None,
        "matched_rule_facts": matched,
        "total_rule_facts": len(facts),
        "literal_citations": literal,
        "invalid_citations": invalid,
        "uncited_claims": uncited,
        "all_versions_match": body["product_version"] == request["product_version"]
        and all(h["product_version"] == request["product_version"] for h in hits.values()),
        "required_evidence_coverage": len(cited & set(label["required_evidence_ids"]))
        / len(label["required_evidence_ids"])
        if label["required_evidence_ids"]
        else None,
        "model_supported_claims": sum(c["support"]["verdict"] == "supported" for c in claims),
        "total_claims": len(claims),
        "forbidden_output_absent": not any(
            re.search(p, c["text"], re.I) for p in label["forbidden_patterns"] for c in claims
        ),
        "current_incident_unconfirmed": body["current_incident_verified"] is False,
    }


def summarize(rows):
    if not rows:
        raise ValueError("不能省略评测任务分母。")
    completed = [r for r in rows if r["status"] == "completed"]
    measured = sorted(r["latency_ms"] for r in rows if r["latency_ms"] is not None)
    known = [r["usage"] for r in rows if r["usage"] is not None]
    return {
        "total": len(rows),
        **{s: sum(r["status"] == s for r in rows) for s in ("completed", "failed", "not_run")},
        "outcome_match_all": sum(r["metrics"]["outcome_match"] for r in completed) / len(rows),
        "rule_fact_coverage_all": sum(r["metrics"]["rule_fact_coverage"] or 0 for r in completed)
        / len(rows),
        "p95_ms": measured[math.ceil(len(measured) * 0.95) - 1] if measured else None,
        **{
            key: sum(u[key] for u in known)
            for key in ("input_tokens", "output_tokens", "known_model_calls", "unknown_usage_calls")
        },
        "cost_cny": None,
        "human_reviewed": False,
        "quality_method": "source_rule_draft_not_semantic_accuracy",
        "unknown_pipeline_attempts": sum(r.get("pipeline_usage_unknown", False) for r in rows),
    }


class Metrics(Contract):
    outcome_match: bool
    rule_fact_coverage: float | None = Field(ge=0, le=1)
    matched_rule_facts: int = Field(ge=0, strict=True)
    total_rule_facts: int = Field(ge=0, strict=True)
    literal_citations: int = Field(ge=0, strict=True)
    invalid_citations: int = Field(ge=0, strict=True)
    uncited_claims: int = Field(ge=0, strict=True)
    all_versions_match: bool
    required_evidence_coverage: float | None = Field(ge=0, le=1)
    model_supported_claims: int = Field(ge=0, strict=True)
    total_claims: int = Field(ge=0, strict=True)
    forbidden_output_absent: bool
    current_incident_unconfirmed: bool

    @model_validator(mode="after")
    def consistent(self):
        if (
            self.model_supported_claims > self.total_claims
            or self.uncited_claims > self.total_claims
        ):
            raise ValueError("模型支持 / 未引用结论不能超过结论总数。")
        if self.matched_rule_facts > self.total_rule_facts:
            raise ValueError("匹配事实不能超过初稿事实总数。")
        if self.total_rule_facts == 0:
            if self.rule_fact_coverage is not None:
                raise ValueError("无事实题的覆盖率应为 null。")
        elif self.rule_fact_coverage is None or not math.isclose(
            self.rule_fact_coverage, self.matched_rule_facts / self.total_rule_facts, abs_tol=1e-12
        ):
            raise ValueError("规则覆盖率必须与原子事实计数一致。")
        return self


class Usage(Contract):
    input_tokens: int = Field(ge=0, strict=True)
    output_tokens: int = Field(ge=0, strict=True)
    known_model_calls: int = Field(ge=0, strict=True)
    unknown_usage_calls: int = Field(ge=0, strict=True)
    cost_cny: None = None


class Row(Contract):
    task_id: Identifier
    split: Literal["dev", "holdout"]
    category: Literal["fact", "injection", "wrong_version", "no_evidence"]
    status: Literal["completed", "failed", "not_run"]
    latency_ms: float | None = Field(ge=0)
    error_code: Identifier | None
    metrics: Metrics | None
    usage: Usage | None
    reference_readback: bool | None
    pipeline_usage_unknown: bool = False

    @model_validator(mode="after")
    def measured(self):
        if self.pipeline_usage_unknown and self.status != "failed":
            raise ValueError("整条管道用量未知只能标记失败尝试，不能猜测实际模型调用数。")
        if self.status == "not_run" and any(
            x is not None
            for x in (
                self.latency_ms,
                self.error_code,
                self.metrics,
                self.usage,
                self.reference_readback,
            )
        ):
            raise ValueError("未运行不能填测量值。")
        if self.status != "not_run" and (self.latency_ms is None or self.usage is None):
            raise ValueError("已尝试必须记录耗时与用量状态。")
        if self.status == "failed" and (not self.error_code or self.metrics is not None):
            raise ValueError("失败必须有错误且没有成功指标。")
        if self.status == "completed" and (self.error_code is not None or self.metrics is None):
            raise ValueError("完成必须有指标且没有错误。")
        return self


class Report(Contract):
    schema_version: Literal["supportops.answer-report.v1"]
    dataset_sha256: Digest
    labels_sha256: Digest
    corpus_sha256: Digest
    index_id: UUID
    main_model: Identifier
    light_model: Identifier
    embedding_model: Identifier
    parameters: Literal["temperature=0;max_tokens=3500/2000;no_retry"]
    transport: Literal["real_http_real_bailian", "test_protocol"]
    task_ids: list[Identifier] = Field(min_length=1, max_length=120)
    attempts: list[Row] = Field(min_length=1, max_length=120)
    human_reviewed: Literal[False] = False
    rescored_from_sha256: Digest | None = None

    @model_validator(mode="after")
    def complete(self):
        ids = [row.task_id for row in self.attempts]
        if (
            len(set(ids)) != len(ids)
            or len(set(self.task_ids)) != len(self.task_ids)
            or (set(ids) != set(self.task_ids))
        ):
            raise ValueError("报告必须包含所有唯一任务，包括失败与未运行。")
        if not any(row.split == "dev" for row in self.attempts):
            raise ValueError("本轮报告需要开发集。")
        return self

    def summary(self):
        rows = [r.model_dump(mode="json") for r in self.attempts]
        return {"all": summarize(rows), "dev": summarize([r for r in rows if r["split"] == "dev"])}
