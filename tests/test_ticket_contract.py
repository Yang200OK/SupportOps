"""首轮输入契约测试；REQ 编号对应规格和验收记录。"""

import pytest
from fastapi.testclient import TestClient

from supportops.api.app import create_app


@pytest.fixture
def client():
    with TestClient(create_app()) as value:
        yield value


@pytest.fixture
def payload():
    return {
        "title": "升级后投递请求超时",
        "description": "RelayDesk 1.1 返回 RD_TIMEOUT，delivery_timeout_ms=2000。",
        "product": "relaydesk",
        "product_version": "1.1",
        "environment": "local_lab",
        "source_type": "synthetic_case",
    }


def test_req001_liveness_does_not_claim_downstream_readiness(client):
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"service": "supportops", "status": "alive", "version": "0.1.0"}


@pytest.mark.parametrize("version", ["1.0", "1.1", "2.0"])
def test_req002_known_versions_produce_unpersisted_drafts(client, payload, version):
    payload["product_version"] = version
    response = client.post("/api/tickets/validate", json=payload)
    assert response.status_code == 200
    assert response.json() == {
        "schema_version": "supportops.ticket-intake.v1",
        "draft": payload,
        "intake_status": "ready_for_intake",
        "missing_fields": [],
        "persisted": False,
    }


def test_req003_missing_version_requests_clarification(client, payload):
    payload.pop("product_version")
    body = client.post("/api/tickets/validate", json=payload).json()
    assert body["intake_status"] == "needs_clarification"
    assert body["missing_fields"] == ["product_version"]
    assert body["draft"]["product_version"] is None
    assert body["persisted"] is False


def test_req003_explicit_unknown_version_is_not_guessed(client, payload):
    payload["product_version"] = None
    body = client.post("/api/tickets/validate", json=payload).json()
    assert body["intake_status"] == "needs_clarification"
    assert body["draft"]["product_version"] is None


def test_req004_trim_edges_preserve_identifiers_and_internal_layout(client, payload):
    payload["title"] = "  中文超时工单  "
    payload["description"] = "\n RD_TIMEOUT\n  delivery_timeout_ms=2000\n"
    response = client.post("/api/tickets/validate", json=payload)
    assert response.status_code == 200
    draft = response.json()["draft"]
    assert draft["title"] == "中文超时工单"
    assert draft["description"] == "RD_TIMEOUT\n  delivery_timeout_ms=2000"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", " \n\t"),
        ("title", "中" * 201),
        ("title", 42),
        ("description", " \n"),
        ("description", "中" * 10001),
        ("description", False),
        ("product", "other-product"),
        ("product_version", "3.0"),
        ("product_version", 1.1),
        ("environment", "production"),
        ("source_type", "real_experiment"),
    ],
    ids=[
        "blank-title",
        "long-title",
        "numeric-title",
        "blank-description",
        "long-description",
        "boolean-description",
        "wrong-product",
        "wrong-version",
        "numeric-version",
        "wrong-environment",
        "wrong-source",
    ],
)
def test_req005_invalid_values_fail_explicitly(client, payload, field, value):
    payload[field] = value
    response = client.post("/api/tickets/validate", json=payload)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "REQUEST_VALIDATION_FAILED"
    assert any(item["field"] == field for item in error["details"])


@pytest.mark.parametrize("field", ["title", "description", "product", "environment", "source_type"])
def test_req005_required_fields_cannot_be_omitted(client, payload, field):
    payload.pop(field)
    response = client.post("/api/tickets/validate", json=payload)
    assert response.status_code == 422
    assert any(item["field"] == field for item in response.json()["error"]["details"])


def test_req005_length_boundaries_are_valid(client, payload):
    payload["title"] = "中" * 200
    payload["description"] = "文" * 10000
    assert client.post("/api/tickets/validate", json=payload).status_code == 200


@pytest.mark.parametrize("field", ["organization_id", "status", "root_cause", "approved_action"])
def test_req006_client_cannot_supply_trusted_or_execution_fields(client, payload, field):
    payload[field] = "untrusted"
    response = client.post("/api/tickets/validate", json=payload)
    assert response.status_code == 422
    assert {"field": field, "type": "extra_forbidden"} in response.json()["error"]["details"]


def test_req007_manual_report_has_explicit_provenance(client, payload):
    payload["source_type"] = "user_report"
    response = client.post("/api/tickets/validate", json=payload)
    assert response.status_code == 200
    assert response.json()["draft"]["source_type"] == "user_report"


def test_req008_malformed_json_has_safe_error_contract(client):
    secret = "sensitive-unfinished-request"
    response = client.post(
        "/api/tickets/validate",
        content='{"description": "' + secret,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "REQUEST_VALIDATION_FAILED"
    assert secret not in response.text


def test_req008_error_does_not_echo_untrusted_input(client, payload):
    secret = "sensitive-body-value"
    payload["organization_id"] = secret
    response = client.post("/api/tickets/validate", json=payload)
    assert response.status_code == 422
    assert secret not in response.text


@pytest.mark.parametrize("value", [None, [], "invalid-body", 42])
def test_req008_non_object_body_is_rejected(client, value):
    response = client.post("/api/tickets/validate", json=value)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "REQUEST_VALIDATION_FAILED"


def test_req009_openapi_describes_contract_and_validation_errors(client):
    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/api/tickets/validate"]["post"]
    assert operation["responses"]["422"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorResponse"
    }
    request = schema["components"]["schemas"]["TicketDraft"]
    assert request["additionalProperties"] is False


def test_req010_validation_does_not_create_ticket_identity(client, payload):
    # 第 2 轮新增正式创建接口；既有 validate 仍只返回未持久化草稿。
    response = client.post("/api/tickets/validate", json=payload)
    assert response.json()["persisted"] is False
    assert "ticket_id" not in response.json()
