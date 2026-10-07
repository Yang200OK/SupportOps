"""检索标签与模型输入分开，来源家族不能跨分区。"""

from uuid import uuid4

import pytest
from pydantic import ValidationError


def dataset():
    return {
        "schema_version": "supportops.retrieval-dataset.v1",
        "index_id": str(uuid4()),
        "corpus_sha256": "a" * 64,
        "tasks": [
            {
                "task_id": "one",
                "source_group": "pool",
                "split": "dev",
                "request": {"query": "连接池等待如何取证", "product_version": "1.1"},
            }
        ],
    }


def test_req607_request_projection_contains_no_labels():
    from supportops.retrieval.dataset import RetrievalDataset

    actual = RetrievalDataset.model_validate(dataset()).tasks[0].request.model_dump(mode="json")
    assert "source_group" not in actual and "expected" not in actual and "split" not in actual


@pytest.mark.parametrize("bad", ["duplicate", "cross_split", "answer"])
def test_req607_invalid_dataset_is_rejected(bad):
    from supportops.retrieval.dataset import RetrievalDataset

    value = dataset()
    task = dict(value["tasks"][0])
    if bad == "duplicate":
        value["tasks"].append(task)
    elif bad == "cross_split":
        value["tasks"].append({**task, "task_id": "two", "split": "holdout"})
    else:
        task["request"]["expected"] = "答案"
    with pytest.raises(ValidationError):
        RetrievalDataset.model_validate(value)


def test_req607_frozen_qrels_match_eligible_sources_and_no_model_labels():
    import json

    from supportops.chunks.chunking import digest
    from supportops.retrieval.dataset import Qrels, RetrievalDataset
    from supportops.settings import ROOT

    root = ROOT / "data/retrieval/baseline-v1"
    dataset = RetrievalDataset.model_validate_json(
        (root / "dataset.json").read_text(encoding="utf-8")
    )
    labels = Qrels.model_validate_json((root / "qrels.json").read_text(encoding="utf-8"))
    snapshot = json.loads((root / "snapshot.json").read_text(encoding="utf-8"))
    assert labels.dataset_sha256 == digest(dataset.model_dump(mode="json"))
    assert dataset.corpus_sha256 == snapshot["index"]["corpus_sha256"]
    tasks = {t.task_id: t for t in dataset.tasks}
    entries = {e["evidence_id"]: e for e in snapshot["entries"]}
    assert set(tasks) == {label.task_id for label in labels.labels}
    for label in labels.labels:
        task = tasks[label.task_id]
        assert not label.human_reviewed
        for evidence_id in label.relevance:
            entry = entries[evidence_id]
            assert entry["product_version"] == task.request.product_version
            assert entry["kind"] in task.request.source_kinds
            if task.request.experiment_ids:
                assert entry["payload"]["source"]["experiment_id"] in map(
                    str, task.request.experiment_ids
                )
    for entry in entries.values():
        assert all(
            key not in entry["payload"]["embedding_text"]
            for key in ("root_cause", "remediation", "fault_family", '"expected"')
        )
