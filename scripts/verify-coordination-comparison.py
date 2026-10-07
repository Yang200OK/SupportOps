"""固定开发集，交替顺序、重新准备每臂；先落盘再派发，无自动重试。"""

import argparse
import importlib.util
import json
from time import monotonic
from uuid import uuid4

import httpx

from supportops.chunks.chunking import digest
from supportops.coordination.comparison import initial_state, metrics
from supportops.rag.model import AnswerModelSettings
from supportops.settings import ROOT

FAMILIES = ("configuration", "pool", "cache", "downstream")
BUDGET = {"tool_calls": 6, "model_calls": 8, "time_budget_ms": 240000, "context_chars": 16000}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--expected-model", required=True)
    args = parser.parse_args()
    output = (ROOT / args.output).resolve()
    if (
        not output.is_relative_to(ROOT / "docs/verification/phase-7-round-3")
        or output.exists()
        or output.suffix != ".json"
    ):
        raise ValueError("只允许本轮目录内新报告，不覆盖任何尝试。")
    model = AnswerModelSettings().main_model
    if model != args.expected_model:
        raise ValueError("当前模型与显式预期不一致。")
    spec = importlib.util.spec_from_file_location(
        "comparison_prepare", ROOT / "scripts/prepare-investigation-lab.py"
    )
    prepare = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prepare)
    frozen = json.loads(
        (ROOT / "data/retrieval/baseline-v1/snapshot.json").read_text(encoding="utf-8")
    )
    accounts = json.loads((ROOT / "local/demo-accounts.json").read_text(encoding="utf-8"))[
        "accounts"
    ]
    report = {
        "workflow": "coordination-comparison.v1",
        "model": model,
        "budget": BUDGET,
        "tasks": [],
        "attempts": [],
        "checks": "running",
        "human_semantic_reviewed": False,
        "cost_cny": None,
    }

    def save():
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with httpx.Client(base_url="http://127.0.0.1:8010", timeout=300, trust_env=False) as client:
        account = next(a for a in accounts if a["username"] == "support_a")
        login = client.post(
            "/api/auth/login",
            json={"username": account["username"], "password": account["password"]},
        )
        login.raise_for_status()
        auth = {"Authorization": "Bearer " + login.json()["access_token"]}

        def call(method, path, body=None):
            response = client.request(method, path, headers=auth, json=body)
            response.raise_for_status()
            return response.json()

        try:
            for family in FAMILIES:
                ticket = call(
                    "POST",
                    "/api/tickets",
                    {
                        "title": "单 / 多 Agent 比较开发任务",
                        "description": (
                            f"RelayDesk 1.1 报告 {prepare.ERRORS[family]}。"
                            "只读当前取证，证据不足保留未决。"
                        ),
                        "product": "relaydesk",
                        "product_version": "1.1",
                        "environment": "local_lab",
                        "source_type": "synthetic_case",
                    },
                )
                report["tasks"].append({"family": family, "ticket": ticket})
            report["tasks_sha256"] = digest(report["tasks"])
            save()
            for ordinal, task in enumerate(report["tasks"]):
                arms = ("single", "multi") if ordinal % 2 == 0 else ("multi", "single")
                for arm in arms:
                    family = task["family"]
                    item = {
                        "family": family,
                        "arm": arm,
                        "task_sha256": digest(task["ticket"]),
                        "index_sha256": frozen["index"]["corpus_sha256"],
                        "model": model,
                        "budget": BUDGET,
                        "response": None,
                        "metrics": None,
                        "status": "preparing",
                    }
                    report["attempts"].append(item)
                    save()
                    try:
                        prepared = output.with_name(output.stem + f"-{family}-{arm}-prepare.json")
                        lab = prepare.prepare(family, prepared)
                        state = initial_state(lab)
                        item.update(
                            prepared_path=str(prepared.relative_to(ROOT)),
                            prepared_sha256=digest(lab),
                            initial_state=state,
                            initial_state_sha256=digest(state),
                            lab_run_id=lab["lab_run_id"],
                        )
                        common = {
                            "index_id": frozen["index"]["index_id"],
                            "lab_run_id": lab["lab_run_id"],
                            "max_tool_calls": 6,
                            "max_model_calls": 8,
                            "time_budget_ms": 240000,
                        }
                        identity = task["ticket"]["ticket_id"]
                        if arm == "multi":
                            board = call(
                                "POST",
                                f"/api/tickets/{identity}/coordination-boards",
                                {**common, "request_id": str(uuid4()), "context_chars": 16000},
                            )
                            body = call(
                                "POST",
                                f"/api/coordination-boards/{board['board_id']}/executions",
                                {"request_id": str(uuid4())},
                            )
                            item.update(
                                board_id=board["board_id"],
                                execution_id=body["execution_id"],
                                waves=[],
                            )
                        item["status"] = "request_started"
                        save()
                        start = monotonic()
                        if arm == "single":
                            body = call(
                                "POST",
                                f"/api/tickets/{identity}/hypothesis-investigations",
                                {
                                    **common,
                                    "use_memory": False,
                                    "use_skills": False,
                                    "use_published_skills": False,
                                },
                            )
                        else:
                            for _ in range(2):
                                body = call(
                                    "POST",
                                    f"/api/coordination-executions/{item['execution_id']}/advance",
                                )
                                item["waves"].append(body)
                                item["response"] = body
                                save()
                                if body["status"] != "pending":
                                    break
                        elapsed = round((monotonic() - start) * 1000)
                        item.update(
                            response=body,
                            duration_ms=elapsed,
                            metrics=metrics(arm, body, elapsed),
                            status=body["status"],
                        )
                        save()
                    except Exception as exc:
                        # 传输未知与准备失败分开保留；不再派发同一请求。
                        item.update(
                            error_type=type(exc).__name__,
                            error_code="HTTP_" + str(exc.response.status_code)
                            if isinstance(exc, httpx.HTTPStatusError)
                            else type(exc).__name__,
                        )
                        if item.get("execution_id"):
                            try:
                                body = call(
                                    "GET", f"/api/coordination-executions/{item['execution_id']}"
                                )
                                elapsed = round((monotonic() - start) * 1000)
                                item.update(
                                    response=body,
                                    duration_ms=elapsed,
                                    metrics=metrics(arm, body, elapsed),
                                )
                            except httpx.HTTPError:
                                pass
                        save()
                    print(
                        json.dumps(
                            {
                                "family": family,
                                "arm": arm,
                                "status": item["status"],
                                "metrics": item["metrics"],
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
            report["checks"] = "attempts_finished"
            save()
        finally:
            prepare.cleanup()
            client.post("/api/auth/logout", headers=auth).raise_for_status()


if __name__ == "__main__":
    main()
