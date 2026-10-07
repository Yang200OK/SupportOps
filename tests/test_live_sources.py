"""固定只读 HTTP 适配的反例；传输替身仅存在测试侧。"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest

from supportops.api.errors import ServiceError
from supportops.investigations.hypothesis_contracts import LabRegistration
from supportops.investigations.live_sources import capture, verify_snapshot


def registration():
    now = datetime.now(timezone.utc)
    return LabRegistration(
        run_id=uuid4(),
        instance_id=uuid4(),
        receiver_instance_id=uuid4(),
        product_version="1.1",
        request_ids=[uuid4()],
        started_at=now - timedelta(seconds=3),
        expires_at=now + timedelta(minutes=8),
        mode="online",
    )


def client(binding, *, foreign=False, unavailable=False, baseline=False):
    def response(request):
        if unavailable:
            return httpx.Response(503)
        if request.url.path == "/diagnostics/state":
            value = (
                {
                    "service": "relaydesk",
                    "instance_id": str(uuid4() if foreign else binding.instance_id),
                    "config": {
                        "product_version": "1.1",
                        "delivery_timeout_ms": 3000,
                        "db_pool_size": 10,
                        "db_pool_wait_ms": 1500,
                        "cache_ttl_ms": 60000,
                    },
                    "checked_out": 0,
                }
                if request.url.port == 8101
                else {
                    "service": "receiver",
                    "instance_id": str(binding.receiver_instance_id),
                    "active_target": "current",
                    "delay_ms": 0,
                }
            )
            return httpx.Response(200, json=value)
        assert (
            request.method == "GET"
            and request.url.path == f"/control/observations/{binding.run_id}"
        )
        value = (
            []
            if request.url.port == 8102
            else [
                {
                    "request_id": str(binding.request_ids[0]),
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                    "phase": "baseline" if baseline else "failure",
                    "service": "relaydesk",
                    "product_version": "1.1",
                    "event": "request_finished",
                    "status": 504,
                    "error_code": "RD_TIMEOUT",
                    "elapsed_ms": 3010.1,
                }
            ]
        )
        return httpx.Response(200, json=value)

    return httpx.Client(
        transport=httpx.MockTransport(response), headers={"X-Lab-Control": "test-only"}
    )


def test_req1304_snapshot_hash_and_failure_only():
    binding = registration()
    with client(binding) as http:
        body = capture(binding, uuid4(), uuid4(), "read_current_observations", http)
    assert len(body["evidence"]) == 1
    verify_snapshot(body)
    assert body["evidence"][0]["source"]["source_type"] == "current_lab_observations"
    body["evidence"][0]["text"] += "tampered"
    with pytest.raises(ServiceError, match="摘要"):
        verify_snapshot(body)
    with client(binding, baseline=True) as http:
        body = capture(binding, uuid4(), uuid4(), "read_current_observations", http)
    assert body["evidence"] == []


@pytest.mark.parametrize(
    "flag,code", [("foreign", "LIVE_INSTANCE_CHANGED"), ("unavailable", "LIVE_SOURCE_UNAVAILABLE")]
)
def test_req1304_instance_and_unavailable_are_not_cause_evidence(flag, code):
    binding = registration()
    with client(binding, **{flag: True}) as http, pytest.raises(ServiceError) as error:
        capture(binding, uuid4(), uuid4(), "read_runtime_state", http)
    assert error.value.code == code


def test_req1304_expired_registration_stops_before_http():
    binding = registration()
    binding = binding.model_copy(
        update={
            "started_at": datetime.now(timezone.utc) - timedelta(minutes=10),
            "expires_at": datetime.now(timezone.utc) - timedelta(seconds=1),
        }
    )
    with client(binding) as http, pytest.raises(ServiceError) as error:
        capture(binding, uuid4(), uuid4(), "read_current_observations", http)
    assert error.value.code == "LIVE_RUN_EXPIRED"
