"""真实 HTTP 导入公共观测，核对隔离、摘要和重启，不上传标签。"""

import argparse
import json
from pathlib import Path

import httpx

from supportops.lab.contracts import LabBundle
from supportops.settings import ROOT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--readback", action="store_true")
    args = parser.parse_args()
    accounts = json.loads((ROOT / "local/demo-accounts.json").read_text(encoding="utf-8"))[
        "accounts"
    ]
    report = {"transport": "real_http", "model_calls": 0, "checks": [], "records": []}
    with httpx.Client(base_url="http://127.0.0.1:8010", timeout=30, trust_env=False) as client:
        tokens = {}
        for account in accounts:
            r = client.post(
                "/api/auth/login", json={key: account[key] for key in ("username", "password")}
            )
            assert r.status_code == 200
            tokens[account["username"]] = {"Authorization": "Bearer " + r.json()["access_token"]}
        auth = tokens["support_a"]
        if args.readback:
            report = json.loads(args.output.read_text(encoding="utf-8"))
            for record in report["records"]:
                response = client.get("/api/experiments/" + record["experiment_id"], headers=auth)
                assert response.status_code == 200
                artifact = LabBundle.model_validate(response.json()["artifact"])
                assert artifact.digest() == record["sha256"]
            report["restart_readback"] = {"status": "passed", "records": len(report["records"])}
        else:
            manifest = json.loads((ROOT / "data/lab/manifest.json").read_text(encoding="utf-8"))
            assert manifest["status"] == "passed"
            for record in manifest["records"]:
                artifact = LabBundle.model_validate_json(
                    (ROOT / "data/lab" / record["path"]).read_text(encoding="utf-8")
                )
                payload = {
                    "artifact": artifact.model_dump(mode="json", exclude_none=True),
                    "sha256": record["sha256"],
                }
                response = client.post("/api/experiments/import", headers=auth, json=payload)
                assert response.status_code == 200
                result = response.json()
                same = client.post("/api/experiments/import", headers=auth, json=payload).json()
                assert same["reused"] and same["experiment_id"] == result["experiment_id"]
                path = "/api/experiments/" + result["experiment_id"]
                detail = client.get(path, headers=auth).json()
                assert LabBundle.model_validate(detail["artifact"]).digest() == artifact.digest()
                assert detail["text_verified"] and detail["execution_verified_by_api"] is False
                assert detail["evidence_ids"] == artifact.evidence_ids()
                assert client.get(path, headers=tokens["support_a2"]).status_code == 200
                assert client.get(path, headers=tokens["support_b"]).status_code == 404
                assert client.get(path).status_code == 401
                payload["artifact"]["fault_family"] = "forged_label"
                assert (
                    client.post("/api/experiments/import", headers=auth, json=payload).status_code
                    == 422
                )
                report["records"].append(
                    {
                        "experiment_id": result["experiment_id"],
                        "run_id": artifact.run_id.hex,
                        "sha256": artifact.digest(),
                        "product_version": artifact.product_version,
                        "observations": len(artifact.observations),
                    }
                )
            report["checks"] = [
                "24_real_bundles",
                "idempotent_import",
                "exact_digest_and_evidence_ids",
                "same_org_shared_cross_org_404",
                "no_auth_401",
                "fault_label_rejected_422",
            ]
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"真实观测 HTTP 核对通过：{len(report['records'])} 个包。")


if __name__ == "__main__":
    main()
