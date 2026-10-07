"""固定旧调查生成方法并测试审批发布；显式模式才产生新模型调用。"""

import argparse
import importlib.util
import json
from datetime import datetime, timezone
from uuid import uuid4

import httpx

from supportops.chunks.chunking import digest
from supportops.rag.model import AnswerModelSettings
from supportops.settings import ROOT
from supportops.skills.paired import aggregate, compare

FAMILIES = ("configuration", "pool", "cache", "downstream")


def previous_module():
    spec = importlib.util.spec_from_file_location(
        "publications_previous", ROOT / "scripts/smoke-hypotheses.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def initial_state(lab):
    if lab["registration"]["mode"] == "startup":
        row = lab["registration"]["boot"]
        return {
            "mode": "startup",
            "exit_code": row["exit_code"],
            "observation": {
                k: v
                for k, v in row["observation"].items()
                if k not in ("request_id", "observed_at", "elapsed_ms")
            },
        }
    product, receiver = lab["actual_states"]
    return {
        "mode": "online",
        "config": product["config"],
        "checked_out": product["checked_out"],
        "active_target": receiver["active_target"],
        "delay_ms": receiver["delay_ms"],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode", choices=("seed", "pairs", "publication", "readback"), required=True
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--expected-model")
    args = parser.parse_args()
    output = (ROOT / args.output).resolve()
    if (
        not output.is_relative_to(ROOT)
        or output.suffix != ".json"
        or output.exists() != (args.mode == "readback")
    ):
        raise ValueError("新运行用项目内新 JSON，读回使用原报告。")
    previous = previous_module()
    prepare = previous.preparation_module()
    frozen = json.loads(
        (ROOT / "data/retrieval/baseline-v1/snapshot.json").read_text(encoding="utf-8")
    )
    accounts = json.loads((ROOT / "local/demo-accounts.json").read_text(encoding="utf-8"))[
        "accounts"
    ]
    report = (
        json.loads(output.read_text(encoding="utf-8"))
        if args.mode == "readback"
        else {
            "transport": "real_local_http_postgres",
            "mode": args.mode,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "model_calls": 0,
            "cost_cny": None,
            "human_semantic_reviewed": False,
        }
    )

    def save():
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with httpx.Client(base_url="http://127.0.0.1:8010", timeout=280, trust_env=False) as client:
        auth = {}
        for name in ("support_a", "support_b"):
            a = next(a for a in accounts if a["username"] == name)
            login = client.post(
                "/api/auth/login", json={"username": name, "password": a["password"]}
            )
            login.raise_for_status()
            auth[name] = {"Authorization": "Bearer " + login.json()["access_token"]}

        def call(method, path, body=None):
            response = client.request(method, path, headers=auth["support_a"], json=body)
            response.raise_for_status()
            return response.json()

        def check_publication(row):
            body = call("GET", f"/api/skill-drafts/{row['draft_id']}")
            assert body == row and digest(body["payload"]) == body["payload_sha256"]
            assert (
                client.get(
                    f"/api/skill-drafts/{row['draft_id']}", headers=auth["support_b"]
                ).status_code
                == 404
            )
            for r in body["reports"]:
                assert digest(r["report"]) == r["sha256"]
            return body

        try:
            save()
            if args.mode == "seed":
                config = json.loads(
                    (
                        ROOT / "docs/verification/phase-6-round-1/live-configuration-attempt2.json"
                    ).read_text(encoding="utf-8")
                )["response"]
                pool = json.loads(
                    (ROOT / "docs/verification/phase-5-round-3/live-pool-attempt1.json").read_text(
                        encoding="utf-8"
                    )
                )["after_restart"]
                old = json.loads(
                    (
                        ROOT / "docs/verification/phase-5-round-2/http-live-plus-attempt4.json"
                    ).read_text(encoding="utf-8")
                )["records"]
                sources = {
                    "configuration": config["investigation_id"],
                    "pool": pool["investigation_id"],
                    **{
                        r["case"]: r["response"]["investigation_id"]
                        for r in old
                        if r["case"] in ("cache", "downstream")
                    },
                }
                report.update(records=[], retrospectives=[])
                for family in FAMILIES:
                    retrospective = call(
                        "POST",
                        f"/api/investigations/{sources[family]}/retrospectives",
                        {"request_id": str(uuid4())},
                    )
                    report["retrospectives"].append(retrospective)
                    c = retrospective["candidate"]
                    assert c and c["eligibility"] == "eligible_unreviewed"
                    if family in ("configuration", "pool"):
                        row = call(
                            "POST",
                            "/api/skill-drafts",
                            {
                                "request_id": str(uuid4()),
                                "candidate_id": c["candidate_id"],
                                "candidate_revision": c["revision"],
                            },
                        )
                        path = f"/api/skill-drafts/{row['draft_id']}"
                        reg = call("POST", path + "/regressions")
                        assert reg["report"]["passed"]
                        row = call(
                            "POST",
                            path + "/decisions",
                            {
                                "request_id": str(uuid4()),
                                "revision": 1,
                                "decision": "approve",
                                "report_id": reg["report_id"],
                                "payload_sha256": row["payload_sha256"],
                                "reason": "认证测试操作者检查结构回归后批准方法，非专家根因审定",
                            },
                        )
                        assert row["status"] == "approved"
                        row = call(
                            "POST", path + "/publish", {"revision": 2, "reason": "本轮显式发布验证"}
                        )
                        if family == "pool":
                            row = call(
                                "POST",
                                path + "/revoke",
                                {"revision": 3, "reason": "验证撤销未来使用，历史保留"},
                            )
                        report["records"].append(check_publication(row))
                    save()
            elif args.mode in ("pairs", "publication"):
                report["transport"] = "real_docker_http_mcp_bailian"
                budget = {"max_tool_calls": 6, "max_model_calls": 8, "time_budget_ms": 240000}
                model = AnswerModelSettings().main_model
                if args.expected_model is None or model != args.expected_model:
                    raise ValueError("真实运行必须明确 expected-model，并与当前配置一致。")
                families = FAMILIES if args.mode == "pairs" else ("configuration",)
                report.update(model=model, budget=budget, tasks=[], attempts=[], pairs=[])
                for ordinal, family in enumerate(families):
                    code = prepare.ERRORS[family]
                    ticket = call(
                        "POST",
                        "/api/tickets",
                        {
                            "title": f"第6阶段成对开发任务 {ordinal + 1}",
                            "description": (
                                f"RelayDesk 1.1 报告 {code}。只读当前取证，证据不足保留未决。"
                            ),
                            "product": "relaydesk",
                            "product_version": "1.1",
                            "environment": "local_lab",
                            "source_type": "synthetic_case",
                        },
                    )
                    mode = "startup" if family == "configuration" else "online"
                    preview = call(
                        "GET", f"/api/tickets/{ticket['ticket_id']}/memory-recall?mode={mode}"
                    )
                    task = {
                        "family_for_harness_only": family,
                        "ticket": ticket,
                        "frozen_memory": preview,
                        "memory_sha256": digest(preview),
                        "task_sha256": digest(ticket),
                        "index_sha256": frozen["index"]["corpus_sha256"],
                        "model": model,
                        "budget": budget,
                    }
                    report["tasks"].append(task)
                report["tasks_sha256"] = digest(report["tasks"])
                report["frozen_at"] = datetime.now(timezone.utc).isoformat()
                save()
                for ordinal, task in enumerate(report["tasks"]):
                    family = task["family_for_harness_only"]
                    groups = (False, True) if ordinal % 2 == 0 else (True, False)
                    if args.mode == "publication":
                        groups = (False,)
                    for memory_on in groups:
                        label = "on" if memory_on else "off"
                        item = {
                            "family_for_harness_only": family,
                            "use_memory": memory_on,
                            "task_sha256": task["task_sha256"],
                            "index_sha256": task["index_sha256"],
                            "model": model,
                            "budget": budget,
                            "memory_sha256": task["memory_sha256"] if memory_on else None,
                            "response": None,
                            "status": "preparing",
                        }
                        report["attempts"].append(item)
                        save()
                        try:
                            lab = prepare.prepare(
                                family,
                                output.with_name(output.stem + f"-{family}-{label}-prepare.json"),
                            )
                            item["initial_state"] = initial_state(lab)
                            item["initial_state_sha256"] = digest(item["initial_state"])
                            item["lab_run_id"] = lab["lab_run_id"]
                            if memory_on:
                                current = call(
                                    "GET",
                                    f"/api/tickets/{task['ticket']['ticket_id']}/memory-recall?mode={lab['registration']['mode']}",
                                )
                                assert current == task["frozen_memory"] and current["loaded"]
                            item["status"] = "request_started"
                            save()
                            body = call(
                                "POST",
                                f"/api/tickets/{task['ticket']['ticket_id']}/hypothesis-investigations",
                                {
                                    "index_id": frozen["index"]["index_id"],
                                    "lab_run_id": lab["lab_run_id"],
                                    "use_memory": memory_on,
                                    "use_skills": False,
                                    "use_published_skills": args.mode == "publication",
                                    **budget,
                                },
                            )
                            item.update(response=body, status=body["status"])
                            save()
                            item["reference_digests"] = previous.audit(
                                client, auth["support_a"], body
                            )
                            assert (
                                client.get(
                                    f"/api/investigations/{body['investigation_id']}",
                                    headers=auth["support_b"],
                                ).status_code
                                == 404
                            )
                            if memory_on:
                                assert body["input_snapshot"]["memory"] == task["frozen_memory"]
                            elif args.mode == "publication":
                                assert body["input_snapshot"]["skills"]["loaded"]
                            item["checks"] = "passed"
                        except Exception as error:
                            item["check_error_type"] = type(error).__name__
                        finally:
                            save()
                            prepare.cleanup()
                    if args.mode == "pairs":
                        pair = [
                            a for a in report["attempts"] if a["family_for_harness_only"] == family
                        ]
                        report["pairs"].append(
                            {
                                "family_for_harness_only": family,
                                **compare(
                                    next(a for a in pair if not a["use_memory"]),
                                    next(a for a in pair if a["use_memory"]),
                                ),
                            }
                        )
                        save()
                report["groups"] = {
                    key: aggregate([a for a in report["attempts"] if a["use_memory"] == enabled])
                    for key, enabled in (("off", False), ("on", True))
                }
                report["model_calls"] = sum(
                    a["response"]["model_calls"] for a in report["attempts"] if a["response"]
                )
            else:
                for row in report.get("records", []):
                    check_publication(row)
                for row in report.get("retrospectives", []):
                    assert call("GET", f"/api/retrospectives/{row['retrospective_id']}") == row
                for item in report.get("attempts", []):
                    if item.get("response"):
                        body = item["response"]
                        assert (
                            call("GET", f"/api/investigations/{body['investigation_id']}") == body
                        )
                        assert (
                            previous.audit(client, auth["support_a"], body)
                            == item["reference_digests"]
                        )
                report["restart_readback"] = {"unchanged": True, "new_model_calls": 0}
            entries = []
            for offset in range(0, 502, 100):
                entries.extend(
                    call(
                        "GET",
                        f"/api/retrieval/indexes/{frozen['index']['index_id']}/entries?offset={offset}&limit=100",
                    )["items"]
                )
            assert digest(entries) == digest(frozen["entries"])
            report["frozen_entries_unchanged"] = len(entries)
            report["checks"] = "passed"
            save()
            print(
                json.dumps(
                    {
                        "mode": args.mode,
                        "records": len(report.get("records", [])),
                        "attempts": len(report.get("attempts", [])),
                        "model_calls": report["model_calls"],
                    }
                )
            )
        except Exception as error:
            report["check_error_type"] = type(error).__name__
            save()
            raise
        finally:
            for headers in auth.values():
                client.post("/api/auth/logout", headers=headers)


if __name__ == "__main__":
    main()
