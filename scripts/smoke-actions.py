"""真实模型建议 / 人工批准协议 / 实验效果；显式暂停后重启续测。"""

import argparse
import json
from pathlib import Path
from uuid import uuid4

import httpx

from supportops.chunks.chunking import digest
from supportops.settings import ROOT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--family", choices=("pool", "cache", "downstream"))
    parser.add_argument("--continue-run", action="store_true")
    parser.add_argument("--readback-completed", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT) or output.exists() != args.continue_run:
        raise ValueError("新尝试使用项目内新 JSON，续测使用原报告。")
    private = ROOT / "local" / (output.stem + "-session.json")
    if args.continue_run:
        report = json.loads(output.read_text(encoding="utf-8"))
        auth = json.loads(private.read_text(encoding="utf-8"))
    else:
        if not args.family:
            raise ValueError("新尝试须明确实验家族，仅测试准备器接收标签。")
        report = {"family_for_harness_only": args.family, "status": "started", "attempts": []}
        account = next(
            a
            for a in json.loads((ROOT / "local/demo-accounts.json").read_text(encoding="utf-8"))[
                "accounts"
            ]
            if a["username"] == "support_a"
        )
        with httpx.Client(timeout=10, trust_env=False) as client:
            response = client.post(
                "http://127.0.0.1:8010/api/auth/login",
                json={"username": account["username"], "password": account["password"]},
            )
            response.raise_for_status()
            auth = {"Authorization": "Bearer " + response.json()["access_token"]}
        private.write_text(json.dumps(auth), encoding="utf-8")

    def save():
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
        )

    save()
    with httpx.Client(
        base_url="http://127.0.0.1:8010", timeout=300, trust_env=False, headers=auth
    ) as client:

        def call(path, payload=None):
            response = client.post(path, json=payload)
            value = response.json()
            report["attempts"].append(
                {"path": path, "status_code": response.status_code, "body": value}
            )
            save()
            response.raise_for_status()
            return value

        try:
            if not args.continue_run:
                # 标签仅在可信准备器，后续模型只看到错误现象和本次原始证据。
                import importlib.util

                spec = importlib.util.spec_from_file_location(
                    "prepare_lab", ROOT / "scripts/prepare-investigation-lab.py"
                )
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                prepared = module.prepare(
                    args.family, output.with_name(output.stem + "-prepare.json")
                )
                index = client.get("/api/retrieval/indexes?limit=30").json()["items"]
                index_id = next(i["index_id"] for i in index if i["entry_count"] == 502)
                ticket = call(
                    "/api/tickets",
                    {
                        "title": "第5阶段第3轮动作验收 " + str(prepared["run_id"])[:8],
                        "description": (
                            f"RelayDesk 1.1 投递出现 {prepared['probe_error_code']}，"
                            "请调查当前异常，建议有限实验验证。"
                        ),
                        "product": "relaydesk",
                        "product_version": "1.1",
                        "environment": "local_lab",
                        "source_type": "synthetic_case",
                    },
                )
                report["ticket"] = ticket
                investigation = call(
                    f"/api/tickets/{ticket['ticket_id']}/hypothesis-investigations",
                    {"index_id": index_id, "lab_run_id": prepared["lab_run_id"]},
                )
                assert investigation["status"] == "completed", "真实调查未完成，不能继续动作。"
                job = call(
                    f"/api/investigations/{investigation['investigation_id']}/action-proposals",
                    {"request_id": str(uuid4())},
                )
                assert job["status"] == "pending", "真实模型未生成有效待批准建议。"
                report["job_id"] = job["job_id"]
                job = call(
                    f"/api/actions/{job['job_id']}/decision",
                    {"decision": "approve", "proposal_sha256": job["proposal_sha256"]},
                )
                assert job["status"] == "approved"
                job = call(f"/api/actions/{job['job_id']}/advance")
                assert job["status"] == "action_done"
                report.update(status="paused_after_action", before_restart=job)
                save()
                print(json.dumps({"status": report["status"], "job_id": job["job_id"]}))
                return
            job_id = report["job_id"]
            before = client.get(f"/api/actions/{job_id}").json()
            if args.readback_completed:
                assert before["status"] == "completed", "只读核对需要已完成的页面复测。"
                assert (
                    before["checkpoint"]["action_receipt"]
                    == report["before_restart"]["checkpoint"]["action_receipt"]
                )
            else:
                assert before == report["before_restart"], "重启后检查点变化。"
            assert digest(before["proposal"]) == before["proposal_sha256"]
            job = before if args.readback_completed else call(f"/api/actions/{job_id}/advance")
            assert job["status"] == "completed"
            assert job["checkpoint"]["retest_passed"], "实际动作后投递仍失败。"
            assert job["checkpoint"]["action_receipt"] == before["checkpoint"]["action_receipt"]
            assert call(f"/api/actions/{job_id}/advance") == job, "重复推进修改完成记录。"
            response = client.get(f"/api/actions/{job_id}/events")
            response.raise_for_status()
            stream = response.text
            output.with_suffix(".sse.txt").write_text(stream, encoding="utf-8")
            sequences = [int(line[4:]) for line in stream.splitlines() if line.startswith("id: ")]
            assert sequences == list(range(1, job["sequence"] + 1))
            report.update(status="completed", after_restart=job, sse_sequences=sequences)
            save()
            client.post("/api/auth/logout").raise_for_status()
            private.unlink()
            print(
                json.dumps(
                    {
                        "status": "completed",
                        "job_id": job_id,
                        "retest_passed": True,
                        "sse_events": len(sequences),
                    }
                )
            )
        except Exception as error:
            report.update(status="failed", error_type=type(error).__name__)
            save()
            client.post("/api/auth/logout")
            private.unlink(missing_ok=True)
            raise


if __name__ == "__main__":
    main()
