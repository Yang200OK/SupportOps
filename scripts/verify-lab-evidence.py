"""从公开容器日志重建观测包，核对字节摘要与事件身份。"""

import hashlib
import json
from collections import defaultdict
from pathlib import Path

from supportops.lab.contracts import LabBundle
from supportops.settings import ROOT


def read_raw(directory: Path):
    manifest = json.loads((directory / "raw-log-manifest.json").read_text(encoding="utf-8"))
    grouped = defaultdict(list)
    for record in manifest["records"]:
        raw = (directory / record["path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == record["sha256"]
        lines = raw.splitlines()
        assert len(lines) == record["records"]
        for line in lines:
            observation = json.loads(line)
            run_id = observation.pop("run_id")
            grouped[run_id].append(observation)
    return grouped


def main():
    evidence = ROOT / "docs/verification/phase-2-round-3"
    frozen = ROOT / "data/lab"
    initial = read_raw(evidence)
    manifest = json.loads((frozen / "manifest.json").read_text(encoding="utf-8"))
    observations = 0
    for record in manifest["records"]:
        bundle = LabBundle.model_validate_json((frozen / record["path"]).read_bytes())
        assert bundle.digest() == record["sha256"]
        # 首批失败启动未保留独立 stdout；只对两项常驻服务逐事件核对。
        expected = [
            row.model_dump(mode="json", exclude_none=True)
            for row in bundle.observations
            if row.service != "boot"
        ]
        actual = sorted(initial[record["run_id"]], key=lambda row: row["observed_at"])
        assert actual == expected
        jsonl = (frozen / record["jsonl_path"]).read_bytes()
        assert hashlib.sha256(jsonl).hexdigest() == record["jsonl_sha256"]
        observations += len(bundle.observations)

    regression = evidence / "runtime-regression"
    repeated = read_raw(regression)
    matrix = json.loads((regression / "matrix.json").read_text(encoding="utf-8"))
    boot_count = 0
    for record in matrix["records"]:
        rows = repeated[record["run_id"]]
        if "boot_stdout_path" in record:
            raw = (regression / record["boot_stdout_path"]).read_bytes()
            assert hashlib.sha256(raw).hexdigest() == record["boot_stdout_sha256"]
            assert record["boot_exit_code"] == 2
            boot = json.loads(raw)
            assert boot.pop("run_id") == record["run_id"]
            rows.append(boot)
            boot_count += 1
        bundle = LabBundle.model_validate(
            {
                "schema_version": "supportops.lab-observations.v1",
                "run_id": record["run_id"],
                "product_version": record["product_version"],
                "observations": sorted(rows, key=lambda row: row["observed_at"]),
            }
        )
        assert bundle.digest() == record["sha256"]
    assert len(manifest["records"]) == len(matrix["records"]) == 24
    assert boot_count == 6
    report = {
        "status": "passed",
        "initial_bundles": 24,
        "initial_observations": observations,
        "initial_service_events_match_raw_stdout": True,
        "initial_boot_stdout_preserved": False,
        "final_bundles_reconstructed_from_public_raw_logs": 24,
        "final_boot_stdout_verified": boot_count,
        "sha256_verified": True,
        "model_calls": 0,
    }
    (evidence / "evidence-audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("日志交叉核对通过：首批 24 包、最终重建 24 包、6 份失败启动 stdout。")


if __name__ == "__main__":
    main()
