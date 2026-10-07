"""顺序执行有界故障实验，公共观测与评测标签分别保存。"""

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

import httpx
from dotenv import dotenv_values

from supportops.evaluations.contracts import EvaluationDataset
from supportops.lab.contracts import LabBundle, Observation, RelayConfig
from supportops.settings import ROOT

DOCKER = Path(os.environ.get("SUPPORTOPS_DOCKER_EXECUTABLE", "docker"))
FAMILIES = ("configuration", "pool", "cache", "downstream")
ERRORS = {
    "configuration": "RD_CONFIG_INVALID",
    "pool": "RD_POOL_WAIT",
    "cache": "RD_CACHE_STALE",
    "downstream": "RD_TIMEOUT",
}
CAUSES = {
    "configuration": (
        "超时键与实际产品版本不匹配，启动校验拒绝。",
        "根据本版本键名修正配置后重新启动。",
    ),
    "pool": (
        "连接池的全部连接被占用，连接获取等待超过实际配置上限。",
        "释放占用并检查连接生命周期，再按原请求复测。",
    ),
    "cache": (
        "目标配置已更新，但读取的缓存仍属于旧目标或旧代次。",
        "按版本清理旧缓存或传播新代次，再核对目标与复测。",
    ),
    "downstream": (
        "接收器实际等待超过生效的 HTTP 超时。",
        "恢复下游响应延迟，再核对超时与固定请求。",
    ),
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "data/lab")
    args = parser.parse_args()
    root = args.output.resolve()
    if not root.is_relative_to(ROOT):
        raise ValueError("实验输出必须位于本项目。")
    if (root / "manifest.json").exists():
        raise ValueError("拒绝覆盖已冻结实验；请显式使用新的项目内输出目录。")
    (root / "observations").mkdir(parents=True, exist_ok=True)
    credentials = dotenv_values(ROOT / ".env.lab")
    auth = {"X-Lab-Control": credentials["LAB_CONTROL_TOKEN"]}
    records, tasks, labels = [], [], []
    manifest = {
        "schema_version": "supportops.lab-manifest.v1",
        "status": "running",
        "model_calls": 0,
        "records": records,
        "defaults": [],
    }

    def save_manifest():
        (root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    with httpx.Client(timeout=10, trust_env=False) as client:

        def control(service, path, payload=None):
            port = 8101 if service == "product" else 8102
            response = client.post(
                f"http://127.0.0.1:{port}/control/{path}", headers=auth, json=payload
            )
            assert response.status_code == 200, f"实验控制失败：{service}/{path}"
            return response.json()

        def event(run_id, version, phase):
            identity = uuid4()
            response = client.post(
                "http://127.0.0.1:8101/events",
                json={
                    "run_id": str(run_id),
                    "request_id": str(identity),
                    "product_version": version,
                    "phase": phase,
                },
            )
            return identity, response

        def observations(port, run_id):
            response = client.get(
                f"http://127.0.0.1:{port}/control/observations/{run_id}", headers=auth
            )
            assert response.status_code == 200
            return response.json()

        # 真实实例的三个默认配置单独校准，不用故障注入时的缩短等待代替默认契约。
        for version in ("1.0", "1.1", "2.0"):
            expected = RelayConfig.model_validate({"product_version": version}).effective()
            actual = control("product", "reset", {"product_version": version})["config"]
            assert actual == expected
            control("receiver", "settings", {"active_target": "current", "delay_ms": 0})
            _, response = event(uuid4(), version, "baseline")
            assert response.status_code == 200
            manifest["defaults"].append(
                {
                    "product_version": version,
                    "effective_config": actual,
                    "fixed_request_status": response.status_code,
                }
            )

        try:
            for family in FAMILIES:
                for version in ("1.0", "1.1", "2.0"):
                    for variant in (0, 1):
                        run_id = uuid4()
                        timeout = 100 if variant == 0 else 180
                        wait = 120 if variant == 0 else 220
                        delay = 350 if variant == 0 else 600
                        timeout_key = (
                            "downstream_timeout_ms" if version == "2.0" else "delivery_timeout_ms"
                        )
                        config = {
                            "product_version": version,
                            timeout_key: timeout,
                            "db_pool_wait_ms": wait,
                            "cache_ttl_ms": 60000 if variant == 0 else 45000,
                        }
                        control("receiver", "settings", {"active_target": "current", "delay_ms": 0})
                        control("product", "reset", config)
                        baseline_id, baseline = event(run_id, version, "baseline")
                        assert baseline.status_code == 200, "基线请求失败，不能当作故障注入成功。"
                        boot = []
                        boot_details = {}
                        if family == "configuration":
                            failure_id = uuid4()
                            wrong = (
                                "delivery_timeout_ms"
                                if version == "2.0"
                                else "downstream_timeout_ms"
                            )
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
                                "RELAYDESK_CONFIG="
                                + json.dumps({"product_version": version, wrong: timeout}),
                                "-e",
                                "LAB_RUN_ID=" + str(run_id),
                                "-e",
                                "LAB_REQUEST_ID=" + str(failure_id),
                                "relaydesk",
                            ]
                            environment = dict(
                                os.environ,
                                PATH=str(DOCKER.parent) + os.pathsep + os.environ["PATH"],
                            )
                            result = subprocess.run(
                                command,
                                cwd=ROOT,
                                env=environment,
                                capture_output=True,
                                timeout=30,
                            )
                            assert result.returncode == 2, "错误配置没有拒绝独立容器启动。"
                            boot = [
                                json.loads(line)
                                for line in result.stdout.splitlines()
                                if line.startswith(b"{")
                            ]
                            assert len(boot) == 1 and boot[0].pop("run_id") == str(run_id)
                            boot_path = "observations/" + str(run_id) + ".boot.jsonl"
                            (root / boot_path).write_bytes(result.stdout)
                            boot_details = {
                                "boot_exit_code": result.returncode,
                                "boot_stdout_path": boot_path,
                                "boot_stdout_sha256": hashlib.sha256(result.stdout).hexdigest(),
                            }
                            failed_status = boot[0]["status"]
                        else:
                            if family == "pool":
                                held = control("product", "pool/hold")
                                assert (
                                    held["checked_out"]
                                    == RelayConfig.model_validate(config).db_pool_size
                                )
                            elif family == "cache":
                                control("product", "target", {"target": "new", "invalidate": False})
                                control(
                                    "receiver", "settings", {"active_target": "new", "delay_ms": 0}
                                )
                            else:
                                control(
                                    "receiver",
                                    "settings",
                                    {"active_target": "current", "delay_ms": delay},
                                )
                            failure_id, failure = event(run_id, version, "failure")
                            assert failure.json()["error_code"] == ERRORS[family]
                            failed_status = failure.status_code
                            if family == "pool":
                                control("product", "pool/release")
                            elif family == "cache":
                                control("product", "cache/clear")
                            else:
                                # 等待真实接收器完成本次已超时请求，保存后半段观测。
                                deadline = monotonic() + 5
                                while not any(
                                    o["request_id"] == str(failure_id)
                                    and o["event"] == "downstream_finished"
                                    for o in observations(8102, run_id)
                                ):
                                    if monotonic() > deadline:
                                        raise AssertionError("接收器超时后的完成观测缺失。")
                                    sleep(0.05)
                                control(
                                    "receiver",
                                    "settings",
                                    {"active_target": "current", "delay_ms": 0},
                                )
                        retest_id, retest = event(run_id, version, "retest")
                        assert retest.status_code == 200, "恢复后固定请求未通过。"
                        entries = observations(8101, run_id) + observations(8102, run_id) + boot
                        entries.sort(key=lambda o: o["observed_at"])
                        artifact = LabBundle(
                            run_id=run_id,
                            product_version=version,
                            observations=[Observation.model_validate(o) for o in entries],
                        )
                        endings = {
                            o.phase: o
                            for o in artifact.observations
                            if o.event in ("request_finished", "config_checked")
                        }
                        assert endings["failure"].error_code == ERRORS[family]
                        if family == "pool":
                            assert endings["failure"].elapsed_ms >= wait * 0.9
                            assert (
                                endings["failure"].checked_out
                                == RelayConfig.model_validate(config).db_pool_size
                            )
                        if family == "cache":
                            observed = next(
                                o
                                for o in artifact.observations
                                if o.phase == "failure" and o.event == "target_observed"
                            )
                            assert observed.db_generation > observed.cache_generation
                            assert observed.db_target != observed.cache_target
                        if family == "downstream":
                            assert endings["failure"].elapsed_ms >= timeout * 0.9
                            observed = next(
                                o
                                for o in artifact.observations
                                if o.phase == "failure" and o.event == "downstream_finished"
                            )
                            assert observed.elapsed_ms >= delay * 0.9
                        name = "observations/" + str(run_id)
                        (root / (name + ".json")).write_text(
                            artifact.canonical() + "\n", encoding="utf-8"
                        )
                        raw = "".join(
                            json.dumps(
                                {
                                    "evidence_id": identity,
                                    **entry.model_dump(mode="json", exclude_none=True),
                                },
                                ensure_ascii=False,
                                sort_keys=True,
                            )
                            + "\n"
                            for identity, entry in zip(
                                artifact.evidence_ids(), artifact.observations, strict=True
                            )
                        )
                        (root / (name + ".jsonl")).write_text(raw, encoding="utf-8", newline="\n")
                        records.append(
                            {
                                "run_id": str(run_id),
                                "product_version": version,
                                "path": name + ".json",
                                "sha256": artifact.digest(),
                                "jsonl_path": name + ".jsonl",
                                "jsonl_sha256": hashlib.sha256(
                                    (root / (name + ".jsonl")).read_bytes()
                                ).hexdigest(),
                                "baseline_status": baseline.status_code,
                                "failure_status": failed_status,
                                "retest_status": retest.status_code,
                                "request_ids": [str(baseline_id), str(failure_id), str(retest_id)],
                                **boot_details,
                            }
                        )
                        evidence = [
                            i
                            for i, o in zip(
                                artifact.evidence_ids(), artifact.observations, strict=True
                            )
                            if o.phase == "failure"
                        ]
                        task_id = str(uuid4())
                        split = "holdout" if family == "cache" else "dev"
                        tasks.append(
                            {
                                "task_id": task_id,
                                "source_group": family,
                                "split": split,
                                "input": {
                                    "title": "事件投递或启动未完成",
                                    "description": (
                                        f"由真实实验整理的构造工单：版本 {version}。"
                                        f"正常请求 HTTP 200，本次操作返回 {failed_status}。"
                                        f"观测运行标识 {run_id}。"
                                        "请结合对应原始观测核对原因与建议，不能仅凭错误码判断。"
                                    ),
                                    "product": "relaydesk",
                                    "product_version": version,
                                    "environment": "local_lab",
                                    "source_type": "synthetic_case",
                                },
                                "expected": {
                                    "outcome": "supported_answer",
                                    "evidence_ids": evidence,
                                },
                            }
                        )
                        labels.append(
                            {
                                "task_id": task_id,
                                "run_id": str(run_id),
                                "fault_family": family,
                                "variant": variant,
                                "root_cause": CAUSES[family][0],
                                "remediation": CAUSES[family][1],
                                "evidence_ids": evidence,
                                "annotation_origin": "controlled_experiment",
                                "human_reviewed": False,
                            }
                        )
                        save_manifest()
                        print(
                            f"真实实验完成 {len(records)}/24：{version}，三阶段均已记录。",
                            flush=True,
                        )
        except Exception:
            manifest["status"] = "failed"
            save_manifest()
            raise
        finally:
            control("product", "pool/release")
            control("receiver", "settings", {"active_target": "current", "delay_ms": 0})
            control("product", "reset", {"product_version": "1.1"})
        dataset = EvaluationDataset.model_validate(
            {
                "schema_version": "supportops.evaluation-dataset.v1",
                "dataset_id": "relaydesk-lab-v1",
                "tasks": tasks,
            }
        )
        assert len(tasks) == 24 and len(labels) == 24
        (root / "evaluation-dataset.v1.json").write_text(
            dataset.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        (root / "evaluation-labels.v1.json").write_text(
            json.dumps(
                {"schema_version": "supportops.lab-labels.v1", "labels": labels},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        manifest.update(status="passed", dataset_sha256=dataset.digest(), dev=18, holdout=6)
        save_manifest()
        print("24 次真实实验、24 个标注任务已完成，未调用模型。")


if __name__ == "__main__":
    main()
