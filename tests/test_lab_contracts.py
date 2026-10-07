"""实验配置、观测包和任务必须保持版本与标签边界。"""

import hashlib
from uuid import uuid4

import pytest
from pydantic import ValidationError

from supportops.lab.contracts import LabBundle, Observation, RelayConfig


@pytest.mark.parametrize(
    "version,key,timeout,pool,wait",
    [
        ("1.0", "delivery_timeout_ms", 2000, 5, 1000),
        ("1.1", "delivery_timeout_ms", 3000, 10, 1500),
        ("2.0", "downstream_timeout_ms", 3000, 10, 1500),
    ],
)
def test_req502_defaults_match_fixed_design(version, key, timeout, pool, wait):
    value = RelayConfig.model_validate({"product_version": version})
    assert value.effective()[key] == timeout
    assert value.db_pool_size == pool and value.db_pool_wait_ms == wait
    assert value.cache_ttl_ms == 60000


@pytest.mark.parametrize(
    "payload",
    [
        {"product_version": "2.0", "delivery_timeout_ms": 2000},
        {"product_version": "1.0", "downstream_timeout_ms": 2000},
        {"product_version": "2.0", "db_pool_size": 0},
        {"product_version": "2.0", "downstream_timeout_ms": "3000"},
        {"product_version": "2.0", "arbitrary_url": "http://example.com"},
    ],
)
def test_req502_invalid_config_never_uses_compatibility(payload):
    with pytest.raises(ValidationError):
        RelayConfig.model_validate(payload)


def bundle():
    run_id = uuid4()
    return LabBundle(
        run_id=run_id,
        product_version="1.1",
        observations=[
            Observation(
                request_id=uuid4(),
                phase=phase,
                service="relaydesk",
                event="request_finished",
                product_version="1.1",
                status=200 if phase != "failure" else 503,
                error_code=None if phase != "failure" else "RD_POOL_WAIT",
                elapsed_ms=2.0,
            )
            for phase in ("baseline", "failure", "retest")
        ],
    )


@pytest.mark.parametrize(
    "field", ["fault_kind", "expected", "controls", "split", "organization_id"]
)
def test_req506_bundle_rejects_control_and_answer_labels(field):
    value = bundle().model_dump(mode="json")
    value[field] = "forged"
    with pytest.raises(ValidationError):
        LabBundle.model_validate(value)


def test_req505_bundle_digest_and_evidence_identity():
    value = bundle()
    assert len(value.evidence_ids()) == 3
    assert all(str(value.run_id) in i for i in value.evidence_ids())
    assert value.digest() == hashlib.sha256(value.canonical().encode()).hexdigest()
    original = value.digest()
    value.observations[0].elapsed_ms = 3.0
    assert value.digest() != original


def test_req505_missing_phase_or_mixed_version_is_rejected():
    value = bundle().model_dump(mode="json")
    value["observations"].pop()
    with pytest.raises(ValidationError):
        LabBundle.model_validate(value)
    value = bundle().model_dump(mode="json")
    value["observations"][0]["product_version"] = "2.0"
    with pytest.raises(ValidationError):
        LabBundle.model_validate(value)
