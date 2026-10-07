"""用户切换主模型后的独立配置验证；原六个派发前失败永远保留。"""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from time import perf_counter

import httpx

from supportops.chunks.chunking import digest
from supportops.evaluations.final import bind_manifest, grid, summarize
from supportops.models.provider import ModelSettings
from supportops.rag.model import AnswerModelSettings
from supportops.settings import ROOT

DIRECTORY = ROOT / "docs/verification/phase-8-round-1/configuration-repair"


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def preparation_failures(manifest, original):
    expected = [r for r in grid(manifest, "agents") if r["task_id"] == "prospective-configuration"]
    rows = [r for r in original["attempts"] if r["task_id"] == "prospective-configuration"]

    def key(row):
        return row["repeat"], row["variant"]

    if len(rows) != 6 or [key(r) for r in rows] != [key(r) for r in expected]:
        raise ValueError("补充验证需要六个原始配置槽位，不能删减或重复。")
    if any(
        r["status"] != "failed"
        or r.get("response") is not None
        or r.get("lab_run_id")
        or r.get("initial_state_sha256")
        for r in rows
    ):
        raise ValueError("只能补充确定未派发的准备失败，拒绝任何可能派发的尝试。")
    return expected


def check_cohort(manifest):
    bind_manifest(manifest)
    for name, sha in manifest["files"].items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != sha:
            raise ValueError("新模型补充配置冻结变化：" + name)
    if ModelSettings().model_dump(mode="json", exclude={"api_key"}) != manifest["models"]:
        raise ValueError("新模型补充配置变化。")
    if AnswerModelSettings().timeout_seconds != manifest["answer_timeout_seconds"]:
        raise ValueError("回答超时变化。")


def freeze_cohort(original, original_report):
    # 用户更换主模型后建立新队列，绝不改写原冻结或将新旧成功率合并。
    models = ModelSettings().model_dump(mode="json", exclude={"api_key"})
    assert models["main_model"] == "qwen3.6-plus"
    assert {k: v for k, v in models.items() if k != "main_model"} == {
        k: v for k, v in original["models"].items() if k != "main_model"
    }
    cohort = {k: v for k, v in original.items() if k != "sha256"}
    current = {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in original["files"]
    }
    changes = {
        name: {"before": sha, "after": current[name]}
        for name, sha in original["files"].items()
        if sha != current[name]
    }
    assert set(changes) == {"src/supportops/models/provider.py", "scripts/run-final-evaluation.py"}
    amendment = load(ROOT / "data/evaluations/final-v1/harness-amendment.json")
    bind_manifest(amendment)
    assert amendment["manifest_sha256"] == original["sha256"]
    assert (
        changes["scripts/run-final-evaluation.py"]
        == amendment["changes"]["scripts/run-final-evaluation.py"]
    )
    name = "scripts/verify-final-configuration.py"
    current[name] = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
    cohort.update(
        schema_version="supportops.new-model-configuration-cohort.v1",
        parent_manifest_sha256=original["sha256"],
        original_agents_sha256=digest(original_report),
        source_changes=changes,
        models=models,
        files=current,
        frozen_at_utc=datetime.now(timezone.utc).isoformat(),
        cohort_boundary="已暴露配置题的独立运行验证；主模型已变，不作同模型消融或盲测结论。",
    )
    cohort["sha256"] = digest(cohort)
    check_cohort(cohort)
    return cohort


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--readback", choices=("before-restart", "after-restart"))
    args = parser.parse_args()
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "final_runner", ROOT / "scripts/run-final-evaluation.py"
    )
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    runner.DIRECTORY = DIRECTORY
    parent = load(ROOT / "data/evaluations/final-v1/manifest.json")
    bind_manifest(parent)
    output = DIRECTORY / "agents.json"
    if output.exists() != bool(args.readback):
        raise ValueError("补充实跑不覆盖；读回只使用已经存在的补充报告。")
    original = load(ROOT / "docs/verification/phase-8-round-1/agents.json")
    assert original["checks"] == "attempts_finished"
    rows = preparation_failures(parent, original)
    # 先核对失败发生在 Docker 冷启动阶段，零模型，且没有登记本次运行。
    for row in rows:
        prefix = f"{row['task_id']}-r{row['repeat']}-{row['variant']}"
        proof = load(ROOT / f"docs/verification/phase-8-round-1/{prefix}-prepare.json")
        assert proof["status"] == "preparation_failed" and proof["model_calls"] == 0
        assert proof["boot_exit_code"] == 1 and "lab_run_id" not in proof
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    cohort_path = DIRECTORY / "manifest.json"
    if args.readback:
        manifest = load(cohort_path)
    else:
        if cohort_path.exists():
            raise ValueError("不覆盖新模型补充冻结。")
        manifest = freeze_cohort(parent, original)
        runner.save(cohort_path, manifest)
    check_cohort(manifest)
    report = (
        load(output)
        if args.readback
        else {
            "schema_version": "supportops.new-model-configuration-validation.v1",
            "manifest_sha256": manifest["sha256"],
            "parent_manifest_sha256": parent["sha256"],
            "models": manifest["models"],
            "original_agents_sha256": digest(original),
            "attempts": rows,
            "checks": "running",
            "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
            "supplement_source_sha256": hashlib.sha256(
                (ROOT / "scripts/verify-final-configuration.py").read_bytes()
            ).hexdigest(),
            "environment_change": (
                "允许访问 Docker 引擎；用户指定主模型改为 qwen3.6-plus；任务、标签和预算未改变。"
            ),
            "original_preparation_failures": 6,
            "original_model_calls_for_these_failures": 0,
            "comparison_policy": (
                "原 184 槽位完整保留，六次补充独立展示，不替换原失败或混入原成功率。"
            ),
        }
    )
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    if not args.readback:
        runner.save(output, report)
    accounts = load(ROOT / "local/demo-accounts.json")["accounts"]
    with httpx.Client(base_url="http://127.0.0.1:8010", timeout=300, trust_env=False) as client:
        auth = []
        try:
            for name in ("support_a", "support_b") if args.readback else ("support_a",):
                account = next(a for a in accounts if a["username"] == name)
                login = client.post(
                    "/api/auth/login", json={"username": name, "password": account["password"]}
                )
                login.raise_for_status()
                auth.append({"Authorization": "Bearer " + login.json()["access_token"]})
            client.headers.update(auth[0])
            if args.readback:
                proof = {
                    "records": {},
                    "references": {},
                    "model_calls": 0,
                    "original_agents_sha256": digest(original),
                }
                assert report["original_agents_sha256"] == digest(original)
                for row in report["attempts"]:
                    response = client.get(row["record_path"])
                    response.raise_for_status()
                    assert response.json() == row["response"]
                    assert client.get(row["record_path"], headers=auth[1]).status_code == 404
                    assert (
                        runner.audit_agent(client, row["response"], row["variant"])
                        == row["references"]
                    )
                    proof["records"][row["record_path"]] = digest(row["response"])
                    proof["references"].update(row["references"])
                destination = DIRECTORY / f"readback-{args.readback}.json"
                if destination.exists():
                    raise ValueError("不覆盖补充读回证据。")
                runner.save(destination, proof)
                print(
                    json.dumps(
                        {
                            "records": len(proof["records"]),
                            "references": len(proof["references"]),
                            "model_calls": 0,
                        }
                    )
                )
                return
            task = next(t for t in manifest["tasks"]["agents"] if t["family"] == "configuration")
            for row in report["attempts"]:
                check_cohort(manifest)
                row.update(
                    task_sha256=digest(task["ticket"]),
                    index_sha256=manifest["index"]["corpus_sha256"],
                    model=manifest["models"]["main_model"],
                    budget=manifest["agent_budget"],
                )
                started = perf_counter()
                try:
                    runner.execute_agent(client, manifest, task, row, output, report)
                except (httpx.HTTPError, ValueError, KeyError, AssertionError, RuntimeError) as exc:
                    row.update(
                        status="failed",
                        failure_type=type(exc).__name__,
                        error_code=row.get("error_code") or "HARNESS_" + type(exc).__name__,
                        duration_ms=row.get("duration_ms")
                        or round((perf_counter() - started) * 1000),
                        quality=None,
                    )
                finally:
                    runner.save(output, report)
                print(
                    json.dumps({k: row[k] for k in ("repeat", "variant", "status", "error_code")}),
                    flush=True,
                )
            report["checks"] = "attempts_finished"
            report["groups"] = {
                variant: summarize([r for r in report["attempts"] if r["variant"] == variant])
                for variant in ("single", "memory", "multi")
            }
            runner.save(output, report)
        finally:
            if not args.readback:
                runner.module("prepare-investigation-lab").cleanup()
            for headers in auth:
                client.post("/api/auth/logout", headers=headers).raise_for_status()


if __name__ == "__main__":
    main()
