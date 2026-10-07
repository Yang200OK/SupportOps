"""冻结的真实观测与独立标注有可核对身份，但不代表模型质量。"""

import hashlib
import json

from supportops.evaluations.contracts import EvaluationDataset
from supportops.lab.contracts import LabBundle, RelayConfig
from supportops.settings import ROOT


def read(name):
    return json.loads((ROOT / "data/lab" / name).read_text(encoding="utf-8"))


def test_req505_frozen_observations_and_jsonl_are_exact():
    manifest = read("manifest.json")
    assert manifest["status"] == "passed" and len(manifest["records"]) == 24
    assert manifest["model_calls"] == 0
    for record in manifest["records"]:
        bundle = LabBundle.model_validate(read(record["path"]))
        assert bundle.digest() == record["sha256"]
        raw = (ROOT / "data/lab" / record["jsonl_path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == record["jsonl_sha256"]
        lines = [json.loads(line) for line in raw.decode().splitlines()]
        assert [line.pop("evidence_id") for line in lines] == bundle.evidence_ids()
        assert lines == [o.model_dump(mode="json", exclude_none=True) for o in bundle.observations]
        assert record["baseline_status"] == 200 and record["retest_status"] == 200
        assert record["failure_status"] >= 400


def test_req507_annotations_have_real_evidence_and_model_projection_has_no_labels():
    manifest = read("manifest.json")
    dataset = EvaluationDataset.model_validate(read("evaluation-dataset.v1.json"))
    labels = read("evaluation-labels.v1.json")["labels"]
    assert len(dataset.tasks) == len(labels) == 24
    assert dataset.digest() == manifest["dataset_sha256"]
    assert sum(t.split == "dev" for t in dataset.tasks) == 18
    bundles = {r["run_id"]: LabBundle.model_validate(read(r["path"])) for r in manifest["records"]}
    mapping = {label["task_id"]: label for label in labels}
    for task in dataset.tasks:
        label = mapping[task.task_id]
        assert set(label["evidence_ids"]) == set(task.expected.evidence_ids)
        assert set(task.expected.evidence_ids) <= set(bundles[label["run_id"]].evidence_ids())
        assert task.split == ("holdout" if label["fault_family"] == "cache" else "dev")
        assert task.input.source_type == "synthetic_case"
        assert set(task.model_input()) == {"task_id", "input"}
        projected = json.dumps(task.model_input())
        assert all(
            key not in projected
            for key in (
                "fault_family",
                "root_cause",
                "remediation",
                "source_group",
                "expected",
                "holdout",
            )
        )
        assert label["human_reviewed"] is False


def test_req502_runtime_defaults_and_normal_cache_update_match_versions():
    report = read("runtime-contract-checks.json")
    for record in report["records"]:
        version = record["product_version"]
        assert (
            record["effective_config"]
            == RelayConfig.model_validate({"product_version": version}).effective()
        )
        assert record["normal_target_update_status"] == (409 if version == "1.0" else 200)
        assert record["after_manual_clear_status"] == 200
