"""固定动作的真实 PostgreSQL / Redis / HTTP 验证，不调用模型。"""

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import httpx
from dotenv import dotenv_values

from supportops.actions.contracts import LabCommand
from supportops.chunks.chunking import digest
from supportops.settings import ROOT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    output = parser.parse_args().output.resolve()
    if not output.is_relative_to(ROOT):
        raise ValueError("验证只写入本项目路径。")
    if output.exists():
        raise ValueError("不能覆盖已有实验验证。")
    report = {"model_calls": 0, "cases": [], "status": "started"}

    def save():
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    with httpx.Client(
        timeout=15,
        trust_env=False,
        headers={"X-Lab-Control": dotenv_values(ROOT / ".env.lab")["LAB_CONTROL_TOKEN"]},
    ) as client:

        def control(port, path, payload=None):
            value = client.post(f"http://127.0.0.1:{port}/control/{path}", json=payload)
            value.raise_for_status()
            return value.json()

        def state(port):
            value = client.get(f"http://127.0.0.1:{port}/diagnostics/state")
            value.raise_for_status()
            return value.json()

        def command(operation, timeout=None):
            product = state(8101)
            return LabCommand(
                action_id=uuid4(),
                operation=operation,
                instance_id=product["instance_id"],
                state_sha256=digest(
                    {key: product[key] for key in ("instance_id", "config", "checked_out")}
                ),
                receiver_instance_id=state(8102)["instance_id"],
                product_version="1.1",
                timeout_ms=timeout,
                run_id=uuid4(),
                request_id=uuid4(),
            ).model_dump(mode="json")

        def execute(cmd):
            value = client.post("http://127.0.0.1:8101/control/actions", json=cmd)
            value.raise_for_status()
            receipt = value.json()
            assert digest({k: v for k, v in receipt.items() if k != "sha256"}) == receipt["sha256"]
            assert receipt["command_sha256"] == digest(cmd)
            return receipt

        try:
            for operation in ("release_pool", "clear_cache", "set_timeout", "restart_product"):
                control(
                    8101,
                    "reset",
                    {
                        "product_version": "1.1",
                        "delivery_timeout_ms": 100,
                        "db_pool_size": 2,
                        "db_pool_wait_ms": 120,
                    },
                )
                control(8102, "settings", {"active_target": "current", "delay_ms": 0})
                warm = execute(command("retest"))
                assert warm["result"]["response"]["status"] == 200
                if operation in ("release_pool", "restart_product"):
                    control(8101, "pool/hold")
                elif operation == "clear_cache":
                    control(8101, "target", {"target": "new", "invalidate": False})
                    control(8102, "settings", {"active_target": "new", "delay_ms": 0})
                else:
                    control(8102, "settings", {"active_target": "current", "delay_ms": 350})
                failure = execute(command("retest"))
                assert failure["result"]["response"]["status"] != 200
                cmd = command(operation, 800 if operation == "set_timeout" else None)
                with ThreadPoolExecutor(max_workers=2) as executor:
                    first, duplicate = list(executor.map(execute, [cmd, cmd]))
                assert first == duplicate
                # 相同键不允许变换参数，完成回执不会再运行副作用。
                conflict = client.post(
                    "http://127.0.0.1:8101/control/actions", json=cmd | {"run_id": str(uuid4())}
                )
                assert conflict.status_code == 409
                assert conflict.json()["detail"] == "ACTION_KEY_CONFLICT"
                retest = execute(command("retest"))
                assert retest["result"]["response"]["status"] == 200
                assert (
                    len(
                        [
                            o
                            for o in retest["result"]["observations"]
                            if o["event"] == "request_finished"
                        ]
                    )
                    == 1
                )
                report["cases"].append(
                    {
                        "operation": operation,
                        "failure": failure,
                        "command": cmd,
                        "receipt": first,
                        "duplicate_same_receipt": True,
                        "retest": retest,
                    }
                )
                save()
            stale = command("release_pool")
            control(8101, "reset", {"product_version": "1.1"})
            rejected = client.post("http://127.0.0.1:8101/control/actions", json=stale)
            assert (
                rejected.status_code == 409 and rejected.json()["detail"] == "LIVE_INSTANCE_CHANGED"
            )
            assert (
                httpx.get(
                    "http://127.0.0.1:8101/control/actions/" + stale["action_id"], trust_env=False
                ).status_code
                == 403
            )
            report.update(
                status="passed", stale_instance_rejected=True, unauthenticated_rejected=True
            )
            save()
        except Exception as error:
            report.update(status="failed", error_type=type(error).__name__)
            save()
            raise
        finally:
            control(8101, "reset", {"product_version": "1.1"})
            control(8102, "settings", {"active_target": "current", "delay_ms": 0})
    print(
        json.dumps(
            {"status": report["status"], "real_actions": len(report["cases"]), "model_calls": 0}
        )
    )


if __name__ == "__main__":
    main()
