"""真实 HTTP / 视觉模型识别公开像素，每项失败也落盘，不自动重试。"""

import argparse
import base64
import json
from pathlib import Path

import httpx

from supportops.settings import ROOT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--case", choices=["relaydesk-config", "relaydesk-injection", "unsupported"]
    )
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT) or output.exists() or output.suffix != ".json":
        raise ValueError("报告使用项目内新的 JSON 路径。")
    report = {
        "transport": "real_http_real_bailian_pixels",
        "records": [],
        "human_reviewed": False,
        "cost_cny": None,
    }
    accounts = json.loads((ROOT / "local/demo-accounts.json").read_text(encoding="utf-8"))[
        "accounts"
    ]
    account = next(a for a in accounts if a["username"] == "support_a")
    manifest = json.loads((ROOT / "data/screenshots/manifest.json").read_text(encoding="utf-8"))
    with httpx.Client(base_url="http://127.0.0.1:8010", timeout=90, trust_env=False) as client:
        login = client.post(
            "/api/auth/login",
            json={"username": account["username"], "password": account["password"]},
        )
        login.raise_for_status()
        auth = {"Authorization": "Bearer " + login.json()["access_token"]}
        for case, expected in manifest.items():
            if args.case and case != args.case:
                continue
            raw = (ROOT / f"data/screenshots/{case}.png").read_bytes()
            response = client.post(
                "/api/rag/screenshots/extract",
                headers=auth,
                json={"image_base64": base64.b64encode(raw).decode()},
            )
            body = response.json()
            row = {
                "case": case,
                "http_status": response.status_code,
                "response": body,
                "field_checks": None,
            }
            if response.status_code == 200:
                d = body["extraction"]
                row["field_checks"] = {
                    "class_correct": d["recognized"] == expected["expected_recognized"],
                    "version_correct": d["product_version"]
                    == ("1.1" if expected["expected_recognized"] else None),
                    "error_correct": d["error_code"]
                    == ("RD_CONFIG_INVALID" if expected["expected_recognized"] else None),
                    "no_verified_incident": body["current_incident_verified"] is False,
                    "no_persisted_image": body["persisted"] is False,
                }
            report["records"].append(row)
            output.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            print(
                json.dumps(
                    {"case": case, "status": response.status_code, "checks": row["field_checks"]},
                    ensure_ascii=False,
                ),
                flush=True,
            )


if __name__ == "__main__":
    main()
