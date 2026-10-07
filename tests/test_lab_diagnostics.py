"""实际接收器只读接口与控制身份；不访问产品数据库。"""

from fastapi.testclient import TestClient

from supportops.lab.receiver import app


def test_req1304_readonly_diagnostic_epoch_changes_on_actual_settings(monkeypatch):
    monkeypatch.setenv("LAB_CONTROL_TOKEN", "test-only-control")
    with TestClient(app) as client:
        assert client.get("/diagnostics/state").status_code == 403
        auth = {"X-Lab-Control": "test-only-control"}
        before = client.get("/diagnostics/state", headers=auth)
        assert before.status_code == 200
        assert (
            client.post(
                "/control/settings", headers=auth, json={"active_target": "current", "delay_ms": 25}
            ).status_code
            == 200
        )
        after = client.get("/diagnostics/state", headers=auth).json()
        assert after["delay_ms"] == 25
        assert after["instance_id"] != before.json()["instance_id"]
        assert client.post("/diagnostics/state", headers=auth).status_code == 405
