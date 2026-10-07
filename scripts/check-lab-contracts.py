"""核对正常目标更新的版本差异，与故障注入结果分开记录。"""

import json
from uuid import uuid4

import httpx
from dotenv import dotenv_values

from supportops.lab.contracts import RelayConfig
from supportops.settings import ROOT


def main():
    auth = {"X-Lab-Control": dotenv_values(ROOT / ".env.lab")["LAB_CONTROL_TOKEN"]}
    records = []
    with httpx.Client(timeout=10, trust_env=False) as client:

        def control(port, path, data=None):
            r = client.post(f"http://127.0.0.1:{port}/control/{path}", headers=auth, json=data)
            assert r.status_code == 200
            return r.json()

        def event(run_id, version):
            return client.post(
                "http://127.0.0.1:8101/events",
                json={
                    "run_id": str(run_id),
                    "request_id": str(uuid4()),
                    "phase": "baseline",
                    "product_version": version,
                },
            )

        for version in ("1.0", "1.1", "2.0"):
            run_id = uuid4()
            expected = RelayConfig.model_validate({"product_version": version}).effective()
            actual = control(8101, "reset", {"product_version": version})["config"]
            assert actual == expected
            control(8102, "settings", {"active_target": "current", "delay_ms": 0})
            assert event(run_id, version).status_code == 200
            control(8101, "target", {"target": "new", "invalidate": True})
            control(8102, "settings", {"active_target": "new", "delay_ms": 0})
            changed = event(run_id, version)
            assert changed.status_code == (409 if version == "1.0" else 200)
            control(8101, "cache/clear")
            assert event(run_id, version).status_code == 200
            observations = []
            for port in (8101, 8102):
                r = client.get(
                    f"http://127.0.0.1:{port}/control/observations/{run_id}", headers=auth
                )
                assert r.status_code == 200
                observations.extend(r.json())
            records.append(
                {
                    "product_version": version,
                    "effective_config": actual,
                    "normal_target_update_status": changed.status_code,
                    "after_manual_clear_status": 200,
                    "run_id": str(run_id),
                    "observations": observations,
                }
            )
        control(8101, "reset", {"product_version": "1.1"})
        control(8102, "settings", {"active_target": "current", "delay_ms": 0})
    report = {
        "transport": "real_http",
        "checked": ["defaults", "timeout_key", "normal_cache_update"],
        "unverified": ["production_performance", "sla", "distributed_consistency"],
        "records": records,
    }
    (ROOT / "data/lab/runtime-contract-checks.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("三版本默认参数、超时键与正常缓存更新真实核对通过。")


if __name__ == "__main__":
    main()
