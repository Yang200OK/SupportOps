"""上下文覆盖不能伪装成新增检索命中。"""

from supportops.retrieval.contexts import covered_ids


def test_req806_only_complete_spans_same_snapshot_are_covered():
    entries = [
        {
            "evidence_id": "a",
            "kind": "document",
            "payload": {"source": {"chunk_set_id": "set", "spans": [{"start": 4, "end": 8}]}},
        },
        {
            "evidence_id": "b",
            "kind": "document",
            "payload": {"source": {"chunk_set_id": "set", "spans": [{"start": 7, "end": 11}]}},
        },
        {
            "evidence_id": "c",
            "kind": "document",
            "payload": {"source": {"chunk_set_id": "other", "spans": [{"start": 4, "end": 8}]}},
        },
    ]
    result = {
        "items": [{"evidence_id": "anchor"}],
        "contexts": [
            {
                "kind": "parent",
                "source": {"chunk_set_id": "set"},
                "start": 0,
                "end": 10,
                "anchor_evidence_ids": ["anchor"],
            }
        ],
    }
    assert covered_ids(result, entries) == {"anchor", "a"}
    assert result["items"] == [{"evidence_id": "anchor"}]
    assert covered_ids({"items": [{"evidence_id": "b"}]}, entries) == {"b"}
