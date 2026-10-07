"""上下文来自已核对的原文区间，命中锚点不因扩展而更换。"""

import hashlib
from uuid import UUID

from supportops.api.errors import ServiceError
from supportops.chunks import service as chunks


def budget_error():
    return ServiceError(409, "CONTEXT_BUDGET_EXCEEDED", "上下文预算不足以完整保留命中片段。")


def text_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def covered_ids(result, entries):
    # 仅供离线上下文消融，不修改检索排名或结论支持状态。
    covered = {hit["evidence_id"] for hit in result["items"]}
    for context in result.get("contexts", []):
        if context["kind"] != "parent":
            continue
        for entry in entries:
            source = entry["payload"]["source"]
            spans = source.get("spans", [])
            if (
                entry["kind"] != "log"
                and source.get("chunk_set_id") == context["source"]["chunk_set_id"]
                and spans
                and all(context["start"] <= s["start"] < s["end"] <= context["end"] for s in spans)
            ):
                covered.add(entry["evidence_id"])
    return covered


def assemble(session, principal, hits, expand, parent_max, budget):
    documents, contexts = {}, []
    for hit in hits:
        source = hit["source"]
        if hit["kind"] == "log" or not expand:
            contexts.append(
                {
                    "context_id": hit["evidence_id"],
                    "kind": "log" if hit["kind"] == "log" else "child",
                    "source": source,
                    "text": hit["text"],
                    "anchor_evidence_ids": [hit["evidence_id"]],
                    "text_sha256": text_hash(hit["text"]),
                    "text_verified": True,
                    "support_verified": False,
                    "first_rank": hit["rank"],
                }
            )
            continue
        citation = chunks.citation(
            session, principal, UUID(source["chunk_set_id"]), UUID(source["chunk_id"])
        )
        parent, child = citation["parent"], citation["chunk"]
        key = (source["chunk_set_id"], str(parent.parent_id))
        group = documents.setdefault(key, {"parent": parent, "source": source, "anchors": []})
        start, end = min(s.start for s in child.spans), max(s.end for s in child.spans)
        if end - start > parent_max:
            raise budget_error()
        group["anchors"].append(
            {"id": hit["evidence_id"], "start": start, "end": end, "rank": hit["rank"]}
        )
    clusters = []
    for group in documents.values():
        current = None
        for anchor in sorted(group["anchors"], key=lambda a: (a["start"], a["id"])):
            if (
                current is None
                or max(current["end"], anchor["end"]) - current["start"] > parent_max
            ):
                current = {**group, "start": anchor["start"], "end": anchor["end"], "anchors": []}
                clusters.append(current)
            current["end"] = max(current["end"], anchor["end"])
            current["anchors"].append(anchor)
    minimum = sum(c["end"] - c["start"] for c in clusters) + sum(len(c["text"]) for c in contexts)
    if minimum > budget:
        raise budget_error()
    extra = budget - minimum
    clusters.sort(key=lambda c: min(a["rank"] for a in c["anchors"]))
    for n, cluster in enumerate(clusters):
        parent = cluster["parent"]
        required = cluster["end"] - cluster["start"]
        extra_here = min(
            min(parent_max, parent.end - parent.start) - required, extra // (len(clusters) - n)
        )
        extra -= extra_here
        width = required + extra_here
        start = max(parent.start, min(cluster["start"] - extra_here // 2, parent.end - width))
        end = start + width
        source = {**cluster["source"], "parent_id": str(parent.parent_id)}
        text = parent.text[start - parent.start : end - parent.start]
        contexts.append(
            {
                "context_id": f"parent:{source['chunk_set_id']}:{parent.parent_id}:{start}:{end}",
                "kind": "parent",
                "source": source,
                "start": start,
                "end": end,
                "parent_start": parent.start,
                "parent_end": parent.end,
                "text": text,
                "anchor_evidence_ids": [
                    a["id"] for a in sorted(cluster["anchors"], key=lambda a: a["rank"])
                ],
                "text_sha256": text_hash(text),
                "text_verified": True,
                "support_verified": False,
                "first_rank": min(a["rank"] for a in cluster["anchors"]),
            }
        )
    contexts.sort(key=lambda c: c["first_rank"])
    for context in contexts:
        context.pop("first_rank")
    assert sum(len(c["text"]) for c in contexts) <= budget
    return contexts
