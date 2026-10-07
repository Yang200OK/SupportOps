"""可信测试准备与 Agent 分开：实际制造故障、探测，再登记只读范围。"""

import argparse
import hashlib
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

import httpx
from dotenv import dotenv_values
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from supportops.investigations.hypothesis_contracts import LabRegistration
from supportops.investigations.live_service import register_lab_run
from supportops.settings import ROOT

DOCKER = Path(os.environ.get("SUPPORTOPS_DOCKER_EXECUTABLE", "docker"))
FAMILIES = ("configuration", "pool", "cache", "downstream", "missing")
ERRORS = {
    "configuration": "RD_CONFIG_INVALID",
    "pool": "RD_POOL_WAIT",
    "cache": "RD_CACHE_STALE",
    "downstream": "RD_TIMEOUT",
}


def control(client, auth, port, path, payload):
    response = client.post(f"http://127.0.0.1:{port}/control/{path}", headers=auth, json=payload)
    response.raise_for_status()
    return response.json()


def cleanup():
    config = dotenv_values(ROOT / ".env.lab")
    with httpx.Client(timeout=10, trust_env=False) as client:
        auth = {"X-Lab-Control": config["LAB_CONTROL_TOKEN"]}
        control(client, auth, 8101, "reset", {"product_version": "1.1"})
        control(client, auth, 8102, "settings", {"active_target": "current", "delay_ms": 0})


def prepare(family, output):
    output = Path(output).resolve()
    if (
        family not in FAMILIES
        or not output.is_relative_to(ROOT)
        or output.suffix != ".json"
        or output.exists()
    ):
        raise ValueError("实验准备必须使用固定场景与项目内新的 JSON 路径。")
    values = dotenv_values(ROOT / ".env")
    lab = dotenv_values(ROOT / ".env.lab")
    accounts = json.loads((ROOT / "local/demo-accounts.json").read_text(encoding="utf-8"))[
        "accounts"
    ]
    account = next(a for a in accounts if a["username"] == "support_a")
    admin_url = values["SUPPORTOPS_ADMIN_DATABASE_URL"]
    if make_url(admin_url).database != "supportops":
        raise ValueError("实验准备只允许本项目 supportops 数据库。")
    run_id, request_id = uuid4(), uuid4()
    started = datetime.now(timezone.utc)
    proof = {
        "family_for_test_harness_only": family,
        "run_id": str(run_id),
        "model_calls": 0,
        "preparation_started_at": started.isoformat(),
    }
    output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        output.write_text(json.dumps(proof, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    save()
    try:
        with httpx.Client(timeout=10, trust_env=False) as client:
            auth = {"X-Lab-Control": lab["LAB_CONTROL_TOKEN"]}
            config = {
                "product_version": "1.1",
                "delivery_timeout_ms": 100,
                "db_pool_size": 2,
                "db_pool_wait_ms": 120,
                "cache_ttl_ms": 60000,
            }
            control(client, auth, 8101, "reset", config)
            control(client, auth, 8102, "settings", {"active_target": "current", "delay_ms": 0})
            baseline_id = uuid4()
            baseline = client.post(
                "http://127.0.0.1:8101/events",
                json={
                    "run_id": str(run_id),
                    "request_id": str(baseline_id),
                    "product_version": "1.1",
                    "phase": "baseline",
                },
            )
            assert baseline.status_code == 200, "正常探测失败，不能开始故障调查。"
            proof["baseline_status"] = baseline.status_code
            if family == "configuration":
                command = [
                    str(DOCKER),
                    "compose",
                    "--env-file",
                    ".env.lab",
                    "-f",
                    "compose.lab.yaml",
                    "run",
                    "--rm",
                    "--no-deps",
                    "-T",
                    "-e",
                    'RELAYDESK_CONFIG={"product_version":"1.1","downstream_timeout_ms":100}',
                    "-e",
                    "LAB_RUN_ID=" + str(run_id),
                    "-e",
                    "LAB_REQUEST_ID=" + str(request_id),
                    "-e",
                    "LAB_DIAGNOSTIC_CONTEXT=1",
                    "relaydesk",
                ]
                environment = dict(
                    os.environ, PATH=str(DOCKER.parent) + os.pathsep + os.environ["PATH"]
                )
                result = subprocess.run(
                    command, cwd=ROOT, env=environment, capture_output=True, timeout=45
                )
                stdout = output.with_suffix(".boot.jsonl")
                stdout.write_bytes(result.stdout)
                proof.update(
                    boot_exit_code=result.returncode,
                    boot_stdout_path=str(stdout.relative_to(ROOT)),
                    boot_stdout_sha256=hashlib.sha256(result.stdout).hexdigest(),
                )
                save()
                assert result.returncode == 2, "错误配置没有实际拒绝启动。"
                events = [
                    json.loads(line) for line in result.stdout.splitlines() if line.startswith(b"{")
                ]
                assert len(events) == 1
                event = events[0]
                assert event.pop("run_id") == str(run_id)
                instance_id = event.pop("instance_id")
                binding = LabRegistration(
                    run_id=run_id,
                    instance_id=instance_id,
                    receiver_instance_id=None,
                    product_version="1.1",
                    request_ids=[request_id],
                    started_at=started,
                    expires_at=started + timedelta(minutes=15),
                    mode="startup",
                    boot={"exit_code": result.returncode, "observation": event},
                )
                proof["probe_error_code"] = event["error_code"]
                proof["raw_failure_observations"] = [event]
            else:
                if family == "pool":
                    held = control(client, auth, 8101, "pool/hold", None)
                    assert held["checked_out"] == 2
                elif family == "cache":
                    control(client, auth, 8101, "target", {"target": "new", "invalidate": False})
                    control(client, auth, 8102, "settings", {"active_target": "new", "delay_ms": 0})
                elif family == "downstream":
                    control(
                        client,
                        auth,
                        8102,
                        "settings",
                        {"active_target": "current", "delay_ms": 350},
                    )
                if family != "missing":
                    response = client.post(
                        "http://127.0.0.1:8101/events",
                        json={
                            "run_id": str(run_id),
                            "request_id": str(request_id),
                            "product_version": "1.1",
                            "phase": "failure",
                        },
                    )
                    proof.update(
                        probe_status=response.status_code,
                        probe_error_code=response.json()["error_code"],
                    )
                    assert proof["probe_error_code"] == ERRORS[family], (
                        "故障没有产生预期的实际信号。"
                    )
                else:
                    proof["probe_error_code"] = None
                rows = []
                for port in (8101, 8102):
                    deadline = monotonic() + 5
                    while True:
                        response = client.get(
                            f"http://127.0.0.1:{port}/control/observations/{run_id}", headers=auth
                        )
                        response.raise_for_status()
                        current = [r for r in response.json() if r["phase"] == "failure"]
                        if (
                            port != 8102
                            or family != "downstream"
                            or any(r["event"] == "downstream_finished" for r in current)
                        ):
                            break
                        if monotonic() >= deadline:
                            raise RuntimeError("下游观测没有在实验等待上限内完成。")
                        sleep(0.05)
                    rows.extend(current)
                states = []
                for port in (8101, 8102):
                    response = client.get(
                        f"http://127.0.0.1:{port}/diagnostics/state", headers=auth
                    )
                    response.raise_for_status()
                    states.append(response.json())
                proof.update(raw_failure_observations=rows, actual_states=states)
                binding = LabRegistration(
                    run_id=run_id,
                    instance_id=states[0]["instance_id"],
                    receiver_instance_id=states[1]["instance_id"],
                    product_version="1.1",
                    request_ids=[request_id],
                    started_at=started,
                    expires_at=started + timedelta(minutes=15),
                    mode="online",
                )
            login = client.post(
                "http://127.0.0.1:8010/api/auth/login",
                json={"username": account["username"], "password": account["password"]},
            )
            login.raise_for_status()
            user_auth = {"Authorization": "Bearer " + login.json()["access_token"]}
            try:
                me = client.get("http://127.0.0.1:8010/api/auth/me", headers=user_auth)
                me.raise_for_status()
                engine = create_engine(admin_url, hide_parameters=True)
                try:
                    with Session(engine) as session, session.begin():
                        record = register_lab_run(session, me.json()["organization_id"], binding)
                        proof.update(
                            lab_run_id=str(record.id),
                            registration_sha256=record.sha256,
                            registration=binding.model_dump(mode="json"),
                            status="registered",
                        )
                finally:
                    engine.dispose()
            finally:
                client.post(
                    "http://127.0.0.1:8010/api/auth/logout", headers=user_auth
                ).raise_for_status()
            save()
            return proof
    except Exception as error:
        proof.update(status="preparation_failed", error_type=type(error).__name__)
        save()
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--family", choices=FAMILIES, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = prepare(args.family, args.output)
    print(
        json.dumps(
            {
                "lab_run_id": result["lab_run_id"],
                "probe_error_code": result["probe_error_code"],
                "model_calls": 0,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
