"""动作权限与幂等恢复的确定性反例。"""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from supportops.actions.contracts import ActionChoice, LabCommand
from supportops.lab.actions import ActionJournal


def test_req1401_no_arbitrary_parameters_or_timeout():
    base = dict(action="clear_cache", timeout_ms=None, reason="核对缓存", evidence_ids=["live:x"])
    ActionChoice(**base)
    for change in ({"url": "http://example.invalid"}, {"action": "shell"}, {"timeout_ms": 800}):
        with pytest.raises(ValidationError):
            ActionChoice(**(base | change))
    with pytest.raises(ValidationError):
        ActionChoice(**(base | {"action": "set_timeout", "timeout_ms": 5001}))


class Store:
    def __init__(self):
        self.values = {}

    def set(self, key, value, nx=False):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def get(self, key):
        return self.values.get(key)


def command():
    return LabCommand(
        action_id=uuid4(),
        operation="release_pool",
        instance_id=uuid4(),
        receiver_instance_id=uuid4(),
        state_sha256="a" * 64,
        product_version="1.1",
        timeout_ms=None,
        run_id=uuid4(),
        request_id=uuid4(),
    )


def test_req1404_completed_receipt_never_repeats_effect():
    journal = ActionJournal(Store())
    cmd = command()
    effects = []
    first = journal.execute(cmd, lambda: effects.append(1) or {"released": True})
    assert journal.execute(cmd, lambda: effects.append(2)) == first
    assert effects == [1]


def test_req1404_incomplete_receipt_uncertain_and_payload_conflict():
    journal = ActionJournal(Store())
    cmd = command()

    def interrupted():
        raise RuntimeError("测试模拟实验中断")

    with pytest.raises(RuntimeError):
        journal.execute(cmd, interrupted)
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as error:
        journal.execute(cmd, lambda: pytest.fail("不能重放未知动作"))
    assert error.value.status_code == 409
    assert error.value.detail == "ACTION_UNCERTAIN"
    with pytest.raises(HTTPException) as error:
        journal.execute(cmd.model_copy(update={"operation": "clear_cache"}), lambda: None)
    assert error.value.detail == "ACTION_KEY_CONFLICT"


def test_req1402_expired_recovery_without_receipt_never_dispatches(monkeypatch):
    import httpx

    from supportops.actions import service
    from supportops.api.errors import ServiceError

    requests = []

    def response(request):
        requests.append(request.method)
        return httpx.Response(404)

    monkeypatch.setattr(
        service, "lab_client", lambda: httpx.Client(transport=httpx.MockTransport(response))
    )
    with pytest.raises(ServiceError) as error:
        service.remote(command(), allow_dispatch=False)
    assert error.value.code == "ACTION_EXPIRED"
    assert requests == ["GET"]
