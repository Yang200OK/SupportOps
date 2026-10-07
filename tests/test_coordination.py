"""任务图、私有包和父预算的失败边界。"""

from copy import deepcopy
from uuid import uuid4

import pytest
from pydantic import ValidationError


def request(**extra):
    from supportops.coordination.contracts import BoardRequest

    return BoardRequest(index_id=uuid4(), lab_run_id=uuid4(), request_id=uuid4(), **extra)


def plan(mode="online", **extra):
    from supportops.coordination.core import build_plan

    return build_plan(
        {"title": "投递失败", "description": "RD_TIMEOUT", "product_version": "1.1"},
        mode,
        request(**extra),
    )


def test_req1802_initial_ready_and_blocked():
    from supportops.coordination.core import ready_tasks

    tasks = plan()["tasks"]
    assert ready_tasks(tasks, {}) == ["documents", "runtime"]
    assert ready_tasks(tasks, {"documents": "completed"}) == ["runtime"]
    assert ready_tasks(tasks, {"documents": "completed", "runtime": "completed"}) == ["synthesis"]
    assert ready_tasks(tasks, {"documents": "failed", "runtime": "completed"}) == []
    assert ready_tasks(tasks, {"documents": "cancelled", "runtime": "completed"}) == []


@pytest.mark.parametrize("fault", ["cycle", "self", "missing", "duplicate", "duplicate_edge"])
def test_req1802_invalid_dag_rejected(fault):
    from supportops.coordination.core import validate_dag

    tasks = deepcopy(plan()["tasks"])
    if fault == "cycle":
        tasks[0]["depends_on"] = ["synthesis"]
    elif fault == "self":
        tasks[0]["depends_on"] = ["documents"]
    elif fault == "missing":
        tasks[0]["depends_on"] = ["absent"]
    elif fault == "duplicate":
        tasks[1]["task_id"] = "documents"
    else:
        tasks[2]["depends_on"] = ["documents", "documents"]
    with pytest.raises(ValueError):
        validate_dag(tasks)


@pytest.mark.parametrize("mode", ["online", "startup"])
def test_req1803_private_packages_and_tools(mode):
    body = plan(mode)
    a, b, c = body["tasks"]
    assert a["package"]["tools"] == ["search_knowledge"]
    assert b["package"]["tools"] == (
        ["read_runtime_state", "read_current_observations"]
        if mode == "online"
        else ["read_startup_diagnostic"]
    )
    assert c["package"]["tools"] == []
    assert "results" not in c["package"]
    for t in body["tasks"]:
        assert (
            not {"organization_id", "session_id", "boot", "url", "fault_family"}
            & t["package"].keys()
        )
    a["package"]["ticket"]["title"] = "changed"
    assert b["package"]["ticket"]["title"] == "投递失败"


@pytest.mark.parametrize("tools,models,chars", [(6, 8, 16000), (2, 3, 300), (4, 5, 8000)])
def test_req1804_allocations_fit_parent(tools, models, chars):
    body = plan(max_tool_calls=tools, max_model_calls=models, context_chars=chars)
    for key, maximum in [("tool_calls", tools), ("model_calls", models), ("context_chars", chars)]:
        assert sum(t["package"]["budget"][key] for t in body["tasks"]) == maximum
    assert body["tasks"][2]["package"]["budget"]["tool_calls"] == 0


@pytest.mark.parametrize(
    "extra",
    [
        {"organization_id": "x"},
        {"tasks": []},
        {"max_tool_calls": 7},
        {"max_model_calls": 2},
        {"context_chars": 16001},
        {"time_budget_ms": 240001},
    ],
)
def test_req1801_1804_request_cannot_expand(extra):
    with pytest.raises(ValidationError):
        request(**extra)
