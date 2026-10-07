"""最终评测不能删失败、择优重复或伪造已知用量。"""

from copy import deepcopy

import pytest

from supportops.chunks.chunking import digest
from supportops.evaluations.final import (
    bind_manifest,
    grid,
    paired_conditions,
    summarize,
    usage,
    validate_report,
)


def manifest():
    value = {
        "schema_version": "supportops.final-freeze.v1",
        "repeats": 2,
        "tasks": {"retrieval": [{"task_id": "r"}], "answers": [], "agents": []},
        "files": {"src/example.py": "a" * 64},
        "models": {"main_model": "fixed"},
    }
    return {**value, "sha256": digest(value)}


def test_req2101_manifest_change_rejected():
    frozen = manifest()
    bind_manifest(frozen)
    frozen["models"]["main_model"] = "changed"
    with pytest.raises(ValueError, match="冻结"):
        bind_manifest(frozen)


def test_req2102_missing_duplicate_or_unknown_slot_rejected():
    frozen = manifest()
    rows = grid(frozen, "retrieval")
    assert len(rows) == 12 and all(r["status"] == "not_run" for r in rows)
    report = {"manifest_sha256": frozen["sha256"], "suite": "retrieval", "attempts": rows}
    validate_report(frozen, report)
    for changed in (rows[:-1], rows + [rows[0]], [{**rows[0], "variant": "new"}, *rows[1:]]):
        with pytest.raises(ValueError, match="网格"):
            validate_report(frozen, {**report, "attempts": changed})


def test_req2103_failed_and_unattempted_are_full_denominator():
    rows = grid(manifest(), "retrieval")
    rows[0].update(
        status="completed",
        duration_ms=20,
        quality={"recall": 0.5},
        usage={"known_calls": 1, "unknown_calls": 0, "input_tokens": 10, "output_tokens": 0},
    )
    rows[1].update(status="failed", duration_ms=15, error_code="MODEL_TIMEOUT")
    rows[2].update(status="request_started")
    value = summarize(rows)
    assert value["total"] == 12 and value["completed"] == 1 and value["not_run"] == 9
    assert value["quality_all"]["recall"] == pytest.approx(0.5 / 12)
    assert value["unknown_pipeline_attempts"] == 2
    assert value["input_tokens"] == 10 and value["human_gold_accuracy"] is None
    assert value["cost_cny"] is None


def test_req2103_retrieval_and_partial_failure_usage_not_double_counted():
    raw = {
        "usage": {"input_tokens": 30, "model_called": True},
        "retrieval_usage": {"input_tokens": 10, "model_called": True},
        "rerank_usage": {"input_tokens": 20, "model_called": True},
    }
    assert usage("retrieval", raw, 200) == {
        "known_calls": 2,
        "unknown_calls": 0,
        "input_tokens": 30,
        "output_tokens": 0,
    }
    partial = {"usage": {"input_tokens": 10, "known_model_calls": 1, "unknown_usage_calls": 1}}
    assert usage("retrieval", partial, 503)["unknown_calls"] == 1
    assert usage("retrieval", {"error": {"code": "MODEL_TIMEOUT"}}, 503) is None
    assert (
        usage(
            "answers",
            {
                "usage": {
                    "known_model_calls": 2,
                    "unknown_usage_calls": 0,
                    "input_tokens": 100,
                    "output_tokens": 20,
                }
            },
            200,
        )["known_calls"]
        == 2
    )


def test_req2104_condition_mismatch_blocks_claim():
    left = {
        "task_id": "a",
        "repeat": 1,
        "task_sha256": "task",
        "model": "m",
        "index_sha256": "i",
        "budget": {},
        "initial_state_sha256": "s",
    }
    right = deepcopy(left)
    assert paired_conditions(left, right)["matched"]
    right["initial_state_sha256"] = "changed"
    assert paired_conditions(left, right)["differences"] == ["initial_state_sha256"]
    right.pop("task_sha256")
    assert "task_sha256" in paired_conditions(left, right)["differences"]


def test_req2102_both_repeats_count_no_best_of_selection():
    rows = grid(manifest(), "retrieval")
    for row in rows:
        row.update(
            status="completed" if row["repeat"] == 1 else "failed",
            duration_ms=1,
            quality={"recall": 1.0} if row["repeat"] == 1 else None,
            usage={"known_calls": 1, "unknown_calls": 0, "input_tokens": 2, "output_tokens": 0},
        )
    result = summarize(rows)
    assert result["completed"] == 6 and result["failed"] == 6
    assert result["quality_all"]["recall"] == 0.5 and result["known_calls"] == 12


def harness():
    import importlib.util

    from supportops.settings import ROOT

    spec = importlib.util.spec_from_file_location(
        "final_runner", ROOT / "scripts/run-final-evaluation.py"
    )
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


@pytest.mark.parametrize("variant", ["direct", "guided"])
def test_req2101_2105_only_request_projection_sent_and_intent_saved(monkeypatch, variant):
    import httpx

    runner = harness()
    saved = []
    monkeypatch.setattr(runner, "save", lambda path, body: saved.append(deepcopy(body)))
    task = {
        "task_id": "a",
        "source_group": "held",
        "request": {"query": "产品路径经过哪些步骤", "product_version": "1.1"},
    }
    row = {"variant": variant, "status": "not_run"}
    report = {"attempts": [row]}
    frozen = {"index": {"index_id": "index"}, "labels": {"answers": [{"facts": ["SECRET_LABEL"]}]}}

    def transport(request):
        import json

        assert saved[-1]["attempts"][0]["status"] == "request_started"
        data = json.loads(request.content)
        assert data["query"] == task["request"]["query"]
        assert not {"source_group", "labels", "facts", "expected"} & set(data)
        assert "SECRET_LABEL" not in request.content.decode()
        return httpx.Response(
            503,
            json={
                "error": {"code": "MODEL_TIMEOUT"},
                "stage": "answer",
                "usage": {
                    "known_model_calls": 1,
                    "unknown_usage_calls": 1,
                    "input_tokens": 10,
                    "output_tokens": 5,
                },
            },
        )

    with httpx.Client(base_url="http://local", transport=httpx.MockTransport(transport)) as client:
        runner.execute_rag(client, frozen, task, row, None, report, "answers")
    assert row["status"] == "failed" and row["error_code"] == "MODEL_TIMEOUT"
    assert row["usage"]["known_calls"] == row["usage"]["unknown_calls"] == 1
    assert row["response"]["usage"]["input_tokens"] == 10


def test_req2105_transport_interruption_preserves_dispatch_intent(monkeypatch):
    import httpx

    runner = harness()
    saved = []
    monkeypatch.setattr(runner, "save", lambda path, body: saved.append(deepcopy(body)))
    task = {"task_id": "a", "request": {"query": "缓存", "product_version": "1.1"}}
    row = {"variant": "direct", "status": "not_run", "usage": None}
    report = {"attempts": [row]}

    def transport(request):
        raise httpx.ReadTimeout("测试中断", request=request)

    with httpx.Client(base_url="http://local", transport=httpx.MockTransport(transport)) as client:
        with pytest.raises(httpx.ReadTimeout):
            runner.execute_rag(
                client, {"index": {"index_id": "index"}}, task, row, None, report, "answers"
            )
    assert len(saved) == 1 and saved[0]["attempts"][0]["status"] == "request_started"
    assert saved[0]["attempts"][0]["usage"] is None


@pytest.mark.parametrize(
    "value,expected",
    [
        ("DEPENDENCY_FAILED", "DEPENDENCY_FAILED"),
        ({"code": "MODEL_RESPONSE_INVALID"}, "MODEL_RESPONSE_INVALID"),
        (None, None),
    ],
)
def test_req2103_existing_agent_error_shapes_are_preserved(value, expected):
    assert harness().workflow_error({"error": value, "status": "failed"}) == expected


def test_req2105_unrecorded_source_drift_stops_run(monkeypatch, tmp_path):
    import hashlib
    from types import SimpleNamespace

    runner = harness()
    source = tmp_path / "source.py"
    source.write_text("original", encoding="utf-8")
    frozen = {
        "files": {"source.py": hashlib.sha256(source.read_bytes()).hexdigest()},
        "models": {},
        "answer_timeout_seconds": 60,
    }
    frozen["sha256"] = digest(frozen)
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "ModelSettings", lambda: SimpleNamespace(model_dump=lambda **_: {}))
    monkeypatch.setattr(runner, "AnswerModelSettings", lambda: SimpleNamespace(timeout_seconds=60))
    assert runner.check_freeze(frozen) is None
    source.write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="冻结文件变化"):
        runner.check_freeze(frozen)


def test_req2101_2102_real_frozen_cohort_retains_original_holdout_and_all_slots():
    import json

    from supportops.settings import ROOT

    frozen = json.loads(
        (ROOT / "data/evaluations/final-v1/manifest.json").read_text(encoding="utf-8")
    )
    bind_manifest(frozen)
    assert {k: len(v) for k, v in frozen["tasks"].items()} == {
        "retrieval": 12,
        "answers": 4,
        "agents": 4,
    }
    assert sum(len(grid(frozen, suite)) for suite in frozen["tasks"]) == 184
    assert frozen["prior_task_status_audit"]
    assert all(r["status"] == "not_run" for r in frozen["prior_task_status_audit"])
    for suite in ("retrieval", "answers"):
        assert all(t["split"] == "holdout" for t in frozen["tasks"][suite])
        assert {t["task_id"] for t in frozen["tasks"][suite]} == {
            item["task_id"] for item in frozen["labels"][suite]
        }
