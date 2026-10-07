"""零模型重启读回：只核对已有任务板、包和跨组织边界。"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

from supportops.chunks.chunking import digest
from supportops.settings import ROOT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    expected_path, output = args.expected.resolve(), args.output.resolve()
    if (
        not expected_path.is_relative_to(ROOT)
        or not output.is_relative_to(ROOT)
        or output.suffix != ".json"
        or output.exists()
    ):
        raise ValueError("读回输入和新的输出必须位于本项目目录内。")
    previous = json.loads(expected_path.read_text(encoding="utf-8"))
    accounts = json.loads((ROOT / "local/demo-accounts.json").read_text(encoding="utf-8"))[
        "accounts"
    ]
    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "model_calls": 0,
        "board_id": previous["cancelled"]["board_id"],
    }
    with httpx.Client(base_url="http://127.0.0.1:8010", timeout=15, trust_env=False) as client:
        ready = client.get("/health/ready")
        ready.raise_for_status()
        report["migration"] = ready.json()["migration"]
        for username in ("support_a", "support_b"):
            account = next(a for a in accounts if a["username"] == username)
            login = client.post(
                "/api/auth/login",
                json={"username": account["username"], "password": account["password"]},
            )
            login.raise_for_status()
            auth = {"Authorization": "Bearer " + login.json()["access_token"]}
            try:
                path = f"/api/coordination-boards/{report['board_id']}"
                response = client.get(path, headers=auth)
                if username == "support_b":
                    assert response.status_code == 404
                    assert (
                        client.get(path + "/tasks/documents/package", headers=auth).status_code
                        == 404
                    )
                    report["cross_organization_404"] = True
                    continue
                response.raise_for_status()
                body = response.json()
                assert body == previous["cancelled"]
                assert body["execution_started"] is False
                report["persisted_board_sha256"] = digest(body)
                package_hashes = {}
                for task in body["tasks"]:
                    private = client.get(path + f"/tasks/{task['task_id']}/package", headers=auth)
                    private.raise_for_status()
                    package = private.json()
                    assert package["execution_allowed"] is False
                    assert digest(package["package"]) == package["sha256"] == task["package_sha256"]
                    package_hashes[task["task_id"]] = package["sha256"]
                    if task["task_id"] == "documents":
                        assert package == previous["privatePackage"]
                report["package_hashes"] = package_hashes
                report["events"] = len(body["events"])
            finally:
                client.post("/api/auth/logout", headers=auth).raise_for_status()
    report["status"] = "verified"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "verified", "model_calls": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()
