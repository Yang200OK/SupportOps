"""共享来源元数据只存一次，原始观测正文与归属可逐项还原。"""

import json
from copy import deepcopy

from supportops.chunks.chunking import digest
from supportops.coordination.envelope import expand_source, normalize_live
from supportops.investigations.live_sources import verify_snapshot


def test_req1903_lossless_shared_snapshot_envelope():
    data = [{"event": "pool_wait", "checked_out": 4}, {"event": "state", "available": 0}]
    meta = {
        "source_type": "current_lab_observations",
        "run_id": "fixed-run",
        "captured_at": "2026-10-07T00:00:00Z",
    }
    snapshot = {**meta, "sha256": digest({**meta, "data": data}), "content_sha256": digest(data)}
    value = {
        "snapshot": snapshot,
        "evidence": [
            {
                "evidence_id": f"e{n}",
                "text": json.dumps(item),
                "source": {**meta, "ordinal": n, "snapshot_sha256": snapshot["sha256"]},
                "text_verified": True,
                "reference_url": f"/fixed/{n}",
            }
            for n, item in enumerate(data)
        ],
    }
    original = deepcopy(value)
    projected = normalize_live(value)
    assert value == original
    assert verify_snapshot(projected) == data
    assert len(json.dumps(projected)) < len(json.dumps(original))
    for before, after in zip(original["evidence"], projected["evidence"], strict=True):
        assert expand_source(after, projected["snapshot"]) == before["source"]
        assert before["text"] == after["text"]
        assert before["evidence_id"] == after["evidence_id"]
