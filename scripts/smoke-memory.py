"""固定历史复盘 / 候选治理与显式新调查；每次尝试先落盘，无自动重试。"""

import argparse
import importlib.util
import json
from datetime import datetime, timezone
from uuid import uuid4

import httpx

from supportops.chunks.chunking import digest
from supportops.settings import ROOT


def previous_module():
    spec = importlib.util.spec_from_file_location(
        "memory_previous", ROOT / "scripts/smoke-skills.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.investigation_module()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--seed", action="store_true")
    parser.add_argument("--case", choices=["configuration", "pool"])
    parser.add_argument("--max-model-calls", type=int, default=8, choices=range(1, 9))
    parser.add_argument("--readback", action="store_true")
    args = parser.parse_args()
    output = (ROOT / args.output).resolve()
    if (
        not output.is_relative_to(ROOT)
        or output.suffix != ".json"
        or output.exists() != args.readback
    ):
        raise ValueError("新运行使用项目内新 JSON；读回指定已有报告。")
    if not args.readback and (args.seed == bool(args.case)):
        raise ValueError("明确选择种子治理或一类新调查。")
    previous = previous_module()
    prepare = previous.preparation_module()
    accounts = json.loads((ROOT / "local/demo-accounts.json").read_text(encoding="utf-8"))[
        "accounts"
    ]
    frozen = json.loads(
        (ROOT / "data/retrieval/baseline-v1/snapshot.json").read_text(encoding="utf-8")
    )
    report = (
        json.loads(output.read_text(encoding="utf-8"))
        if args.readback
        else {
            "transport": "real_local_http_postgres",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "status": "started",
            "cost_cny": None,
            "human_semantic_reviewed": False,
            "effectiveness_evaluated": False,
            "model_calls": 0,
        }
    )

    def save():
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with httpx.Client(base_url="http://127.0.0.1:8010", timeout=300, trust_env=False) as client:
        auth = {}
        for name in ("support_a", "support_b"):
            account = next(a for a in accounts if a["username"] == name)
            login = client.post(
                "/api/auth/login", json={"username": name, "password": account["password"]}
            )
            login.raise_for_status()
            auth[name] = {"Authorization": "Bearer " + login.json()["access_token"]}

        def request(method, path, body=None):
            response = client.request(method, path, headers=auth["support_a"], json=body)
            response.raise_for_status()
            return response.json()

        def read_retrospective(identity):
            body = request("GET", f"/api/retrospectives/{identity}?include_source=true")
            assert digest(body["source"]) == body["source_sha256"]
            assert digest(body["summary"]) == body["summary_sha256"]
            candidate = body["candidate"]
            if candidate:
                assert digest(candidate["body"]) == candidate["body_sha256"]
                for e in candidate["body"]["source_evidence"]:
                    reference = request("GET", e["reference_url"])
                    assert reference["text_verified"]
            assert (
                client.get(f"/api/retrospectives/{identity}", headers=auth["support_b"]).status_code
                == 404
            )
            return body

        try:
            save()
            if args.readback:
                if "records" in report:
                    for original in report["records"]:
                        body = read_retrospective(original["retrospective_id"])
                        assert body == original
                if "response" in report:
                    original = report["response"]
                    assert (
                        request("GET", f"/api/investigations/{original['investigation_id']}")
                        == original
                    )
                    assert (
                        previous.audit(client, auth["support_a"], original)
                        == report["reference_digests"]
                    )
                report["restart_readback"] = {"unchanged": True, "model_calls": 0}
            elif args.seed:
                sources = [
                    ("docs/verification/phase-6-round-1/live-configuration-attempt2.json", None),
                    ("docs/verification/phase-5-round-3/live-pool-attempt1.json", "after_restart"),
                    ("docs/verification/phase-5-round-3/live-cache-attempt3.json", "after_restart"),
                ]
                report["records"] = []
                for path, action_key in sources:
                    prior = json.loads((ROOT / path).read_text(encoding="utf-8"))
                    source = prior[action_key] if action_key else prior["response"]
                    body = request(
                        "POST",
                        f"/api/investigations/{source['investigation_id']}/retrospectives",
                        {
                            "request_id": str(uuid4()),
                            "action_id": source["job_id"] if action_key else None,
                        },
                    )
                    report["records"].append(read_retrospective(body["retrospective_id"]))
                    save()
                    assert body["candidate"] and body["model_calls"] == 0
                    if action_key:
                        assert body["summary"]["action"]["retest_passed"] is True
                first = report["records"][0]
                duplicate = request(
                    "POST",
                    f"/api/investigations/{first['investigation_id']}/retrospectives",
                    {"request_id": str(uuid4())},
                )
                left, right = first["candidate"], duplicate["candidate"]
                request(
                    "POST",
                    f"/api/experiences/{left['candidate_id']}/conflicts",
                    {
                        "revision": left["revision"],
                        "other_revision": right["revision"],
                        "other_id": right["candidate_id"],
                        "reason": "验收用人工冲突标注，不是语义检测金标准",
                    },
                )
                conflict_body = read_retrospective(first["retrospective_id"])
                assert conflict_body["candidate"]["eligibility"] == "conflicted"
                request(
                    "POST",
                    f"/api/experiences/{right['candidate_id']}/decisions",
                    {"revision": 2, "decision": "invalidate", "reason": "验收用重复候选不再参考"},
                )
                report["records"][0] = read_retrospective(first["retrospective_id"])
                report["records"].append(read_retrospective(duplicate["retrospective_id"]))
                assert report["records"][0]["candidate"]["eligibility"] == "eligible_unreviewed"
                revoked = request(
                    "POST",
                    f"/api/investigations/{first['investigation_id']}/retrospectives",
                    {"request_id": str(uuid4())},
                )
                request(
                    "POST",
                    f"/api/experiences/{revoked['candidate']['candidate_id']}/decisions",
                    {"revision": 1, "decision": "revoke", "reason": "验收错误经验撤销路径"},
                )
                report["records"].append(read_retrospective(revoked["retrospective_id"]))
                report["conflict_and_invalidation_verified"] = True
                report["revocation_verified"] = True
            else:
                report["transport"] = "real_docker_http_mcp_bailian"
                report["case_for_harness_only"] = args.case
                lab = prepare.prepare(args.case, output.with_name(output.stem + "-prepare.json"))
                ticket = request(
                    "POST",
                    "/api/tickets",
                    {
                        "title": "第6阶段经验验收 " + lab["run_id"][:8],
                        "description": (
                            f"RelayDesk 1.1 本次隔离实验 {lab['probe_error_code']}。"
                            "只读当前取证，证据不足保留未决。"
                        ),
                        "product": "relaydesk",
                        "product_version": "1.1",
                        "environment": "local_lab",
                        "source_type": "synthetic_case",
                    },
                )
                report["ticket"] = ticket
                mode = lab["registration"]["mode"]
                report["recall_preview"] = request(
                    "GET", f"/api/tickets/{ticket['ticket_id']}/memory-recall?mode={mode}"
                )
                save()
                assert report["recall_preview"]["loaded"]
                report["investigation_request_started"] = True
                report["model_calls"] = 0 if args.max_model_calls == 1 else None
                save()
                body = request(
                    "POST",
                    f"/api/tickets/{ticket['ticket_id']}/hypothesis-investigations",
                    {
                        "index_id": frozen["index"]["index_id"],
                        "lab_run_id": lab["lab_run_id"],
                        "use_memory": True,
                        "max_model_calls": args.max_model_calls,
                    },
                )
                report.update(response=body, status=body["status"], model_calls=body["model_calls"])
                save()
                assert body["memory"] == body["input_snapshot"]["memory"]
                assert body["memory"]["loaded"]
                report["reference_digests"] = previous.audit(client, auth["support_a"], body)
                assert (
                    client.get(
                        f"/api/investigations/{body['investigation_id']}", headers=auth["support_b"]
                    ).status_code
                    == 404
                )
                if args.max_model_calls == 1:
                    assert body["model_calls"] == 0 and body["stop_reason"] == "model_budget"
                else:
                    assert body["status"] == "completed", body["stop_reason"]
            entries = []
            for offset in range(0, 502, 100):
                entries.extend(
                    request(
                        "GET",
                        f"/api/retrieval/indexes/{frozen['index']['index_id']}/entries?offset={offset}&limit=100",
                    )["items"]
                )
            assert digest(entries) == digest(frozen["entries"])
            report.update(frozen_entries_unchanged=len(entries))
            report["readback_checks" if args.readback else "checks"] = "passed"
            save()
            print(
                json.dumps(
                    {
                        "checks": "passed",
                        "model_calls": report["model_calls"],
                        "records": len(report.get("records", [])),
                    }
                )
            )
        except Exception as error:
            report["check_error_type"] = type(error).__name__
            if "response" not in report:
                report["status"] = "validation_failed"
            save()
            raise
        finally:
            if args.case and not args.readback:
                prepare.cleanup()
            for headers in auth.values():
                client.post("/api/auth/logout", headers=headers)


if __name__ == "__main__":
    main()
