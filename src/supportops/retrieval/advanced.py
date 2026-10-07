"""新增步骤包装既有检索，仅显式开启时调用排序或扩展。"""

from time import perf_counter

from sqlalchemy import select

from supportops.api.errors import ServiceError
from supportops.chunks.chunking import digest
from supportops.models.provider import ModelFailure, ModelSettings, Provider
from supportops.retrieval import contexts, hybrid, service
from supportops.retrieval.contracts import HybridSearch
from supportops.retrieval.models import RetrievalEntry

OPTIONS = {"rerank", "expand_parent", "rerank_pool", "parent_max_chars", "context_budget_chars"}


class AdvancedFailure(ServiceError):
    def __init__(self, failure, stage, usage):
        super().__init__(failure.status, failure.code, failure.message)
        self.stage = stage
        self.usage = usage


def search(session, principal, index_id, payload):
    started = perf_counter()
    base = HybridSearch.model_validate(payload.model_dump(exclude=OPTIONS))
    if not payload.rerank and not payload.expand_parent:
        return hybrid.search(session, principal, index_id, base)
    try:
        result = hybrid.search(
            session,
            principal,
            index_id,
            base,
            result_limit=payload.rerank_pool if payload.rerank else None,
        )
    except ServiceError as failure:
        if failure.code.startswith("MODEL_"):
            raise AdvancedFailure(
                failure,
                "retrieval",
                {
                    "input_tokens": None,
                    "known_model_calls": 0,
                    "unknown_usage_calls": int(failure.code != "MODEL_NOT_CONFIGURED"),
                    "cost_cny": None,
                },
            ) from None
        raise
    result["retrieval_usage"] = dict(result["usage"])
    result["rerank_usage"] = {"model_called": False, "input_tokens": 0, "cost_cny": None}
    result["advanced_configuration"] = payload.model_dump(include=OPTIONS)
    result["rerank_candidates"] = len(result["items"]) if payload.rerank else 0
    for hit in result["items"]:
        hit["retrieval_rank"] = hit["rank"]
    if payload.rerank and result["items"]:
        settings = ModelSettings()
        result["advanced_configuration"].update(
            rerank_model=settings.rerank_model, rerank_endpoint=settings.rerank_url
        )
        evidence_ids = [h["evidence_id"] for h in result["items"]]
        index = service.get_index(session, principal, index_id)
        rows = (
            session.execute(
                select(RetrievalEntry).where(
                    service.scope_for(principal, index_id, base),
                    RetrievalEntry.evidence_id.in_(evidence_ids),
                )
            )
            .scalars()
            .all()
        )
        by_id = {r.evidence_id: r for r in rows}
        if set(by_id) != set(evidence_ids):
            raise ServiceError(409, "INDEX_INTEGRITY_FAILED", "排序池与固定来源不一致。")
        for row in rows:
            service.verify_entry_hash(
                {
                    key: getattr(row, key)
                    for key in (
                        "evidence_id",
                        "kind",
                        "product_version",
                        "document_id",
                        "experiment_id",
                        "payload",
                    )
                },
                index,
            )
        texts = [by_id[key].payload["embedding_text"] for key in evidence_ids]
        result["rerank_input_sha256"] = digest(
            {"query": base.query, "evidence_ids": evidence_ids, "texts": texts}
        )
        try:
            with Provider(settings) as provider:
                ranking = provider.rerank_texts(base.query, texts)
        except ModelFailure as failure:
            usage = {
                "input_tokens": result["retrieval_usage"]["input_tokens"],
                "known_model_calls": int(result["retrieval_usage"]["model_called"]),
                "unknown_usage_calls": int(failure.code != "MODEL_NOT_CONFIGURED"),
                "cost_cny": None,
            }
            raise AdvancedFailure(service.unavailable(failure), "rerank", usage) from None
        except ValueError:
            raise AdvancedFailure(
                ServiceError(422, "RERANK_INPUT_TOO_LARGE", "排序池文本超过显式预算。"),
                "rerank_input",
                {
                    "input_tokens": result["retrieval_usage"]["input_tokens"],
                    "known_model_calls": int(result["retrieval_usage"]["model_called"]),
                    "unknown_usage_calls": 0,
                    "cost_cny": None,
                },
            ) from None
        for position, score in zip(ranking["indices"], ranking["scores"], strict=True):
            result["items"][position]["rerank_score"] = score
        result["items"].sort(key=lambda h: (-h["rerank_score"], h["evidence_id"]))
        result["items"] = result["items"][: base.top_k]
        result["rerank_usage"] = {
            k: v for k, v in ranking.items() if k not in {"indices", "scores"}
        }
        result["rerank_usage"]["model_called"] = True
        result["usage"] = {
            "input_tokens": result["retrieval_usage"]["input_tokens"] + ranking["input_tokens"],
            "cost_cny": None,
            "model_called": True,
            "model_calls": int(result["retrieval_usage"]["model_called"]) + 1,
        }
    for rank, hit in enumerate(result["items"], 1):
        hit["rank"] = rank
    try:
        result["contexts"] = contexts.assemble(
            session,
            principal,
            result["items"],
            payload.expand_parent,
            payload.parent_max_chars,
            payload.context_budget_chars,
        )
    except ServiceError as failure:
        raise AdvancedFailure(
            failure,
            "context",
            {
                "input_tokens": result["usage"]["input_tokens"],
                "known_model_calls": int(result["retrieval_usage"]["model_called"])
                + int(result["rerank_usage"]["model_called"]),
                "unknown_usage_calls": 0,
                "cost_cny": None,
            },
        ) from None
    for hit in result["items"]:
        hit["context_ids"] = [
            c["context_id"]
            for c in result["contexts"]
            if hit["evidence_id"] in c["anchor_evidence_ids"]
        ]
    result["context_chars"] = sum(len(c["text"]) for c in result["contexts"])
    result["scope"] = payload.model_dump(mode="json", exclude={"query"})
    result["latency_ms"] = round((perf_counter() - started) * 1000, 3)
    return result
