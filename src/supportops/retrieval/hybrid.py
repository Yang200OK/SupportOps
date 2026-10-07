"""复用已验收向量检索，按显式模式增加词法与排名融合。"""

from time import perf_counter

from sqlalchemy import select

from supportops.retrieval import lexical, service
from supportops.retrieval.contracts import Search
from supportops.retrieval.models import RetrievalEntry


def search(session, principal, index_id, payload, *, result_limit=None):
    started = perf_counter()
    base = Search.model_validate(payload.model_dump(exclude={"mode", "candidate_limit"}))
    if result_limit is not None:
        if type(result_limit) is not int or not 1 <= result_limit <= 40:
            raise ValueError("内部排序池必须为 1 至 40。")
        base = base.model_copy(update={"top_k": result_limit})
    parameters = {
        "mode": payload.mode,
        "candidate_limit": payload.candidate_limit,
        "tokenizer": lexical.TOKENIZER,
        "bm25_k1": lexical.K1,
        "bm25_b": lexical.B,
        "bm25_idf": "log1p((N-df+0.5)/(df+0.5))",
        "rrf_k": lexical.RRF_K,
        "tie_break": "evidence_id",
    }
    if payload.mode == "vector":
        result = service.search(session, principal, index_id, base)
        result.update(mode="vector", retrieval_configuration=parameters)
        return result
    index = service.get_index(session, principal, index_id)
    sql_started = perf_counter()
    rows = (
        session.execute(
            select(
                RetrievalEntry.evidence_id,
                RetrievalEntry.kind,
                RetrievalEntry.product_version,
                RetrievalEntry.document_id,
                RetrievalEntry.experiment_id,
                RetrievalEntry.payload,
            ).where(service.scope_for(principal, index_id, base))
        )
        .mappings()
        .all()
    )
    sql_latency = (perf_counter() - sql_started) * 1000
    for row in rows:
        # 统计前校验全部候选，篡改未命中文本也不能影响词频。
        service.verify_entry_hash(row, index)
    corpus = {r["evidence_id"]: r["payload"]["embedding_text"] for r in rows}
    lexical_started = perf_counter()
    keyword = lexical.bm25(corpus, base.query)[: payload.candidate_limit]
    lexical_latency = (perf_counter() - lexical_started) * 1000
    vector = []
    if payload.mode == "rrf":
        # 候选上限已经由接口验证；保持旧向量 SQL 和回查路径。
        result = service.search(
            session, principal, index_id, base.model_copy(update={"top_k": payload.candidate_limit})
        )
        vector = result["items"]
    else:
        result = {
            "index_id": str(index_id),
            "corpus_sha256": index.corpus_sha256,
            "configuration": index.configuration,
            "eligible_count": len(rows),
            "usage": {"input_tokens": 0, "cost_cny": None, "model_called": False},
            "sql_latency_ms": 0.0,
        }
    vector_map = {hit["evidence_id"]: hit for hit in vector}
    vector_ranks = {hit["evidence_id"]: rank for rank, hit in enumerate(vector, 1)}
    keyword_ranks = {key: rank for rank, (key, _) in enumerate(keyword, 1)}
    keyword_scores = dict(keyword)
    ranking = (
        lexical.fuse(list(vector_map), list(keyword_scores)) if payload.mode == "rrf" else keyword
    )
    by_id = {r["evidence_id"]: r for r in rows}
    items = []
    for rank, (key, score) in enumerate(ranking[: base.top_k], 1):
        row = by_id[key]
        service.verify_hit(session, principal, row, index)
        items.append(
            {
                "rank": rank,
                "evidence_id": key,
                "kind": row["kind"],
                "product_version": row["product_version"],
                "text": row["payload"]["text"],
                "source": row["payload"]["source"],
                "reference_url": row["payload"]["reference_url"],
                "vector_rank": vector_ranks.get(key),
                "bm25_rank": keyword_ranks.get(key),
                "cosine_similarity": vector_map.get(key, {}).get("cosine_similarity"),
                "bm25_score": keyword_scores.get(key),
                "rrf_score": score if payload.mode == "rrf" else None,
                "text_verified": True,
                "support_verified": False,
            }
        )
    result.update(
        mode=payload.mode,
        retrieval_configuration=parameters,
        scope=payload.model_dump(mode="json", exclude={"query"}),
        items=items,
        support_verified=False,
        lexical_latency_ms=round(lexical_latency, 3),
        candidate_counts={"vector": len(vector), "bm25": len(keyword)},
        sql_latency_ms=round(result["sql_latency_ms"] + sql_latency, 3),
        latency_ms=round((perf_counter() - started) * 1000, 3),
    )
    return result
