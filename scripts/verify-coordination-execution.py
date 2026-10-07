"""本轮公开自建实验的 HTTP 验证与零模型重启读回，不自动重试。"""

import argparse
import json
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

import httpx

from supportops.chunks.chunking import digest
from supportops.settings import ROOT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared", type=Path)
    parser.add_argument("--readback", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pause-before-merge", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    source = (args.prepared or args.readback).resolve()
    if (
        bool(args.prepared) == bool(args.readback)
        or not source.is_relative_to(ROOT)
        or not output.is_relative_to(ROOT)
        or output.exists()
    ):
        raise ValueError("请指定项目内唯一输入与新的输出文件。")
    previous = json.loads(source.read_text(encoding="utf-8"))
    accounts = json.loads((ROOT / "local/demo-accounts.json").read_text(encoding="utf-8"))[
        "accounts"
    ]
    report = {"status": "started", "model_calls": 0}
    resume = ROOT / "local/coordination-merge-resume.signal"
    if args.pause_before_merge and resume.exists():
        raise ValueError("恢复信号已存在，请使用新的受控验证流程。")

    def save():
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with httpx.Client(base_url="http://127.0.0.1:8010", timeout=250, trust_env=False) as client:
        account = next(a for a in accounts if a["username"] == "support_a")
        login = client.post(
            "/api/auth/login",
            json={"username": account["username"], "password": account["password"]},
        )
        login.raise_for_status()
        auth = {"Authorization": "Bearer " + login.json()["access_token"]}
        identity = None
        try:
            if args.prepared:
                mode = previous["registration"]["mode"]
                ticket = client.post(
                    "/api/tickets",
                    headers=auth,
                    json={
                        "title": "协作调查开发验证",
                        "description": (
                            "RelayDesk 启动校验报告 RD_CONFIG_INVALID，"
                            "需核对 1.1 版本允许的超时配置键。"
                        )
                        if mode == "startup"
                        else (
                            "RelayDesk 投递失败，返回 RD_POOL_WAIT；"
                            "请分别核对版本规则和本次实测信号。"
                        ),
                        "product": "relaydesk",
                        "product_version": "1.1",
                        "environment": "local_lab",
                        "source_type": "synthetic_case",
                    },
                )
                ticket.raise_for_status()
                indexes = client.get("/api/retrieval/indexes?limit=30", headers=auth)
                indexes.raise_for_status()
                index = next(i for i in indexes.json()["items"] if i["entry_count"] == 502)
                board = client.post(
                    f"/api/tickets/{ticket.json()['ticket_id']}/coordination-boards",
                    headers=auth,
                    json={
                        "request_id": str(uuid4()),
                        "index_id": index["index_id"],
                        "lab_run_id": previous["lab_run_id"],
                    },
                )
                board.raise_for_status()
                report["board"] = board.json()
                created = client.post(
                    f"/api/coordination-boards/{board.json()['board_id']}/executions",
                    headers=auth,
                    json={"request_id": str(uuid4())},
                )
                created.raise_for_status()
                identity = created.json()["execution_id"]
                report.update(execution_id=identity, created=created.json(), waves=[])
                save()
                for _ in range(2):
                    advanced = client.post(
                        f"/api/coordination-executions/{identity}/advance", headers=auth
                    )
                    advanced.raise_for_status()
                    value = advanced.json()
                    report["waves"].append(value)
                    report.update(final=value, model_calls=value["usage"]["model_calls"])
                    save()
                    if value["status"] != "pending":
                        break
                    if args.pause_before_merge and len(report["waves"]) == 1:
                        # 令 API 重启发生在持久化阶段之间；凭据仅留在验证进程内存。
                        report["waiting_for_api_restart"] = True
                        save()
                        until = monotonic() + 60
                        while not resume.exists():
                            if monotonic() >= until:
                                raise RuntimeError("本次显式重启验证没有及时给出继续信号。")
                            sleep(0.1)
                        report["waiting_for_api_restart"] = False
            else:
                identity = previous["execution_id"]
                read = client.get(f"/api/coordination-executions/{identity}", headers=auth)
                read.raise_for_status()
                assert read.json() == previous["final"]
                report.update(
                    execution_id=identity,
                    persisted_sha256=digest(read.json()),
                    status="verified",
                    final=read.json(),
                )
            final = report["final"]
            evidence = {}
            for task in final["tasks"].values():
                for item in task["evidence"]:
                    response = client.get(
                        f"/api/coordination-executions/{identity}/evidence/{item['evidence_id']}",
                        headers=auth,
                    )
                    response.raise_for_status()
                    assert response.json()["evidence"]["text"] == item["text"]
                    evidence[item["evidence_id"]] = response.json()
            board_read = client.get(f"/api/coordination-boards/{final['board_id']}", headers=auth)
            board_read.raise_for_status()
            if args.prepared:
                assert board_read.json() == report["board"]
            report.update(evidence=evidence, status="verified", execution_status=final["status"])
            save()
        except Exception as exc:
            report.update(status="verification_failed", error_type=type(exc).__name__)
            if identity:
                read = client.get(f"/api/coordination-executions/{identity}", headers=auth)
                if read.status_code == 200:
                    report.update(
                        final=read.json(), model_calls=read.json()["usage"]["model_calls"]
                    )
            save()
            raise
        finally:
            client.post("/api/auth/logout", headers=auth).raise_for_status()
        other = next(a for a in accounts if a["username"] == "support_b")
        login = client.post(
            "/api/auth/login", json={"username": other["username"], "password": other["password"]}
        )
        login.raise_for_status()
        other_auth = {"Authorization": "Bearer " + login.json()["access_token"]}
        try:
            assert (
                client.get(
                    f"/api/coordination-executions/{identity}", headers=other_auth
                ).status_code
                == 404
            )
            report["cross_organization_404"] = True
            save()
        finally:
            client.post("/api/auth/logout", headers=other_auth).raise_for_status()
    print(
        json.dumps(
            {
                "status": report["status"],
                "execution_status": report["execution_status"],
                "model_calls": report["model_calls"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
