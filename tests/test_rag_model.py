"""使用测试传输核对 JSON 适配和用量，真实百炼另行验证。"""

import json

import httpx
import pytest
from test_model_contract import settings
from test_rag_contracts import draft

from supportops.models.provider import ModelFailure, Provider
from supportops.rag.contracts import AnswerDraft
from supportops.rag.guided_contracts import Rewrite
from supportops.rag.model import AnswerModelSettings, ChatFailure, chat_json


def test_req905_answer_timeout_is_explicit_and_environment_can_shorten(monkeypatch):
    monkeypatch.delenv("SUPPORTOPS_MODEL_TIMEOUT_SECONDS", raising=False)
    assert AnswerModelSettings(_env_file=None).timeout_seconds == 60
    monkeypatch.setenv("SUPPORTOPS_MODEL_TIMEOUT_SECONDS", "15")
    assert AnswerModelSettings(_env_file=None).timeout_seconds == 15


def response():
    return {
        "model": "qwen3.7-plus",
        "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
        "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(draft())}}],
    }


def test_req1001_light_role_is_explicit_and_main_default_is_preserved():
    calls = []
    value = {"action": "retrieve", "query": "RD_TIMEOUT", "questions": [], "reason": "检索错误码。"}

    def send(request):
        body = json.loads(request.content)
        calls.append(body)
        incoming = response()
        incoming["model"] = settings().light_model
        incoming["choices"][0]["message"]["content"] = json.dumps(value)
        return httpx.Response(200, json=incoming)

    with Provider(settings(), transport=httpx.MockTransport(send)) as provider:
        result, usage = chat_json(provider, "规则", {}, Rewrite, 2200, role="light")
    assert result.query == "RD_TIMEOUT" and len(calls) == 1
    assert calls[0]["model"] == settings().light_model == usage["requested_model"]


def test_req902_main_json_interface_and_schema_are_explicit():
    calls = []

    def send(request):
        body = json.loads(request.content)
        calls.append(body)
        assert body["model"] == "qwen3.7-plus" and body["max_tokens"] == 3500
        assert body["response_format"] == {"type": "json_object"}
        assert "JSON Schema" in body["messages"][0]["content"]
        return httpx.Response(200, json=response())

    with Provider(settings(), transport=httpx.MockTransport(send)) as provider:
        result, usage = chat_json(provider, "规则", {"query": "问题"}, AnswerDraft, 3500)
    assert result.claims[0].claim_id == "C1" and usage["input_tokens"] == 10
    assert len(calls) == 1 and usage["parameters"]["enable_thinking"] is False


@pytest.mark.parametrize(
    "change", ["truncated", "invalid_json", "tool_call", "wrong_model", "usage", "extra_field"]
)
def test_req905_invalid_response_stops_and_preserves_known_usage(change):
    body = response()
    if change == "truncated":
        body["choices"][0]["finish_reason"] = "length"
    elif change == "invalid_json":
        body["choices"][0]["message"]["content"] = "bad json upstream secret"
    elif change == "tool_call":
        body["choices"][0]["message"]["tool_calls"] = [{"name": "shell"}]
    elif change == "wrong_model":
        body["model"] = "other"
    elif change == "usage":
        body["usage"]["total_tokens"] = 3
    else:
        output = draft()
        output["root_cause"] = "已确认"
        body["choices"][0]["message"]["content"] = json.dumps(output)
    with Provider(
        settings(), transport=httpx.MockTransport(lambda req: httpx.Response(200, json=body))
    ) as provider:
        with pytest.raises(ChatFailure) as failure:
            chat_json(provider, "规则", {}, AnswerDraft, 3500)
    assert failure.value.code == "MODEL_RESPONSE_INVALID" and "secret" not in str(failure.value)
    assert (failure.value.usage is None) == (change in {"wrong_model", "usage"})


def test_req905_timeout_has_one_attempt_and_no_response_usage():
    seen = []

    def timeout(request):
        seen.append(request)
        raise httpx.ReadTimeout("private transport detail", request=request)

    with Provider(settings(), transport=httpx.MockTransport(timeout)) as provider:
        with pytest.raises(ModelFailure, match="MODEL_TIMEOUT"):
            chat_json(provider, "规则", {}, AnswerDraft, 3500)
    assert len(seen) == 1
