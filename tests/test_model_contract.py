"""模型适配边界使用明确的测试传输，不替代真实模型验收。"""

import json

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from supportops.models.provider import ModelFailure, ModelSettings, Provider


def settings(**changes):
    # 旧响应夹具固定使用原模型，生产配置切换不改变其身份核对测试。
    values = {"main_model": "qwen3.7-plus", **changes}
    return ModelSettings(api_key=SecretStr("test-only-secret"), **values)


def test_current_main_model_uses_user_selected_free_quota(monkeypatch):
    monkeypatch.delenv("SUPPORTOPS_MODEL_MAIN_MODEL", raising=False)
    assert ModelSettings(_env_file=None).main_model == "qwen3.6-plus"


def test_req207_existing_dashscope_environment_is_supported(monkeypatch):
    monkeypatch.delenv("SUPPORTOPS_MODEL_API_KEY", raising=False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-existing-environment")
    assert ModelSettings().api_key.get_secret_value() == "test-existing-environment"
    assert "test-existing-environment" not in repr(ModelSettings())


def test_req207_missing_configuration_fails_before_network():
    with pytest.raises(ModelFailure, match="MODEL_NOT_CONFIGURED"):
        Provider(ModelSettings(api_key=None))


@pytest.mark.parametrize(
    "url",
    [
        "http://dashscope.aliyuncs.com/v1",
        "https://untrusted.example/v1",
        "https://user:password@dashscope.aliyuncs.com/v1",
    ],
)
def test_req207_key_is_never_sent_to_untrusted_or_insecure_endpoint(url):
    with pytest.raises(ValidationError):
        settings(base_url=url)


@pytest.mark.parametrize("status", [401, 429, 500])
def test_req207_http_failure_is_safe_and_has_no_retry(status):
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(
            status, json={"message": "secret upstream response", "key": "test-only-secret"}
        )

    with Provider(settings(), transport=httpx.MockTransport(respond)) as provider:
        with pytest.raises(ModelFailure) as failure:
            provider.chat("main")
    assert len(seen) == 1
    assert "secret" not in str(failure.value)
    assert failure.value.code == "MODEL_HTTP_ERROR"


def test_req207_timeout_does_not_retry_or_switch_model():
    calls = []

    def timeout(request):
        calls.append(request)
        raise httpx.ReadTimeout("test-secret", request=request)

    with Provider(settings(), transport=httpx.MockTransport(timeout)) as provider:
        with pytest.raises(ModelFailure) as failure:
            provider.chat("main")
    assert failure.value.code == "MODEL_TIMEOUT" and len(calls) == 1


@pytest.mark.parametrize(
    "model", ["qwen3.6-plus", "qwen3.7-plus", "qwen-max", "qwen-plus-2025-07-28"]
)
def test_req208_chat_validates_json_usage_and_explicit_model_request(model):
    def respond(request):
        payload = json.loads(request.content)
        assert payload["model"] == model
        assert payload["response_format"] == {"type": "json_object"}
        return httpx.Response(
            200,
            json={
                "model": payload["model"],
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": '{"probe_status":"ok","root_cause":null}'},
                    }
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28},
            },
        )

    with Provider(settings(main_model=model), transport=httpx.MockTransport(respond)) as provider:
        result = provider.chat("main")
    assert result["input_tokens"] == 20 and result["output_tokens"] == 8
    assert result["cost_cny"] is None and result["probe_status"] == "ok"


def test_req208_embedding_reorders_returned_indices_and_checks_dimensions():
    payload = {
        "data": [{"index": 1, "embedding": [0.1, 0.2]}, {"index": 0, "embedding": [0.3, 0.4]}],
        "usage": {"prompt_tokens": 5, "total_tokens": 5},
    }
    with Provider(
        settings(embedding_dimensions=2),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
    ) as provider:
        result = provider.embedding()
    assert result["indices"] == [0, 1] and result["dimensions"] == 2
    payload["data"][1]["index"] = 1
    with Provider(
        settings(embedding_dimensions=2),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
    ) as provider:
        with pytest.raises(ModelFailure) as failure:
            provider.embedding()
    assert failure.value.code == "MODEL_RESPONSE_INVALID"


def test_req208_rerank_preserves_source_indices_and_rejects_invalid_scores():
    payload = {
        "output": {
            "results": [{"index": 1, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.1}]
        },
        "usage": {"total_tokens": 15},
    }
    with Provider(
        settings(),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=json.dumps(payload))
        ),
    ) as provider:
        result = provider.rerank()
    assert result["indices"] == [1, 0] and result["input_tokens"] == 15
    payload["output"]["results"][0]["relevance_score"] = float("inf")
    with Provider(
        settings(),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=json.dumps(payload))
        ),
    ) as provider:
        with pytest.raises(ModelFailure):
            provider.rerank()
