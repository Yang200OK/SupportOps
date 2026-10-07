"""字面保护和来源身份属于应用约束，不能替代语义评测。"""

import copy
import re

from supportops.rag.contracts import AnswerDraft
from supportops.rag.validation import bind_citations

PROTECTED = re.compile(
    r"\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b"
    r"|\b[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+\b"
    r"|(?<![A-Za-z0-9])[-+]?\d+(?:\.\d+)?(?![A-Za-z0-9])"
)


def terms(text):
    return {m.group().casefold() for m in PROTECTED.finditer(text)}


def guard_query(query, original, version, evidence=""):
    required = terms(original)
    actual = terms(query)
    allowed = required | terms(evidence) | {version}
    if not required <= actual or not actual <= allowed:
        raise ValueError("改写丢失原始标识符 / 数值或引入无依据条件。")


def query_key(query):
    return re.sub(r"\s+", "", query).casefold()


def merge_hits(previous, incoming, search_number):
    items = copy.deepcopy(previous)
    by_id = {h["evidence_id"]: h for h in items}
    for hit in items:
        hit.setdefault("search_ranks", [{"search": 1, "rank": hit["rank"]}])
    added = 0
    for hit in incoming:
        key = hit["evidence_id"]
        if key in by_id:
            existing = by_id[key]
            if any(existing[k] != hit[k] for k in ("text", "source", "kind", "product_version")):
                raise ValueError("重复身份的来源或原文不一致。")
            existing["search_ranks"].append({"search": search_number, "rank": hit["rank"]})
        else:
            row = copy.deepcopy(hit)
            row["search_ranks"] = [{"search": search_number, "rank": hit["rank"]}]
            items.append(row)
            by_id[key] = row
            added += 1
    for n, hit in enumerate(items, 1):
        hit["rank"] = n
    return items, added


def validate_conflicts(conflicts, windows, hits):
    by_id = {h["evidence_id"]: h for h in hits}
    output = []
    for n, conflict in enumerate(conflicts, 1):
        row = conflict.model_dump() if hasattr(conflict, "model_dump") else conflict
        draft = AnswerDraft.model_validate(
            {
                "claims": [
                    {
                        "claim_id": f"C{n}",
                        "kind": "fact",
                        "text": row["topic"],
                        "citations": row["citations"],
                    }
                ],
                "missing_information": ["确认来源适用范围。"],
            }
        )
        citations = bind_citations(draft, windows)[0]["citations"]
        sources = [by_id[c["evidence_id"]] for c in citations]
        if (
            len({h["product_version"] for h in sources}) != 1
            or len({h["kind"] for h in sources}) != 1
        ):
            raise ValueError("不同版本或来源角色不能视为同一事实冲突。")
        if sources[0]["kind"] == "log":
            scopes = set()
            for hit in sources:
                source, event = hit["source"], hit["source"]["event"]
                scopes.add(
                    (
                        source["run_id"],
                        *(
                            event.get(k)
                            for k in ("phase", "request_id", "event", "service", "observed_at")
                        ),
                    )
                )
            if len(scopes) != 1:
                raise ValueError("不同实验、阶段或事件不能视为同一观测冲突。")
        if sources[0]["kind"] == "case" and len({h["source"]["document_id"] for h in sources}) != 1:
            raise ValueError("不同历史案例不能视为同一当前状态冲突。")
        revisions = {}
        for hit in sources:
            if hit["kind"] != "log":
                source = hit["source"]
                revisions.setdefault(source["document_id"], set()).add(source.get("revision_id"))
        if any(len(ids) > 1 for ids in revisions.values()):
            raise ValueError("不同修订不能视为同一资料状态冲突。")
        output.append(
            {
                **row,
                "citations": citations,
                "method": "model_conflict_assessment",
                "human_reviewed": False,
            }
        )
    return output
