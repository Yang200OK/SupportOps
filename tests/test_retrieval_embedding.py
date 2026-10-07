"""测试新批量向量边界，保留原固定探测行为。"""

import json

import httpx
import pytest
from test_model_contract import settings

from supportops.models.provider import ModelFailure, Provider


def result():
    return {
        "model": "qwen3.7-text-embedding",
        "data": [{"index": 1, "embedding": [0, 1]}, {"index": 0, "embedding": [1, 0]}],
        "usage": {"prompt_tokens": 7},
    }


def test_req601_batch_maps_each_actual_text_and_usage():
    def respond(request):
        body = json.loads(request.content)
        assert body["input"] == ["数据库连接等待", "下游投递超时"]
        assert body["dimensions"] == 2
        return httpx.Response(200, json=result())

    with Provider(settings(embedding_dimensions=2), httpx.MockTransport(respond)) as p:
        actual = p.embed_texts(["数据库连接等待", "下游投递超时"])
    assert actual["vectors"] == [[1, 0], [0, 1]]
    assert actual["input_tokens"] == 7 and actual["cost_cny"] is None
    assert actual["returned_model"] == "qwen3.7-text-embedding"


@pytest.mark.parametrize(
    "bad", ["duplicate", "missing", "dimension", "zero", "nan", "bool", "usage"]
)
def test_req601_malformed_response_is_rejected(bad):
    body = result()
    if bad == "duplicate":
        body["data"][0]["index"] = 0
    elif bad == "missing":
        body["data"].pop()
    elif bad == "dimension":
        body["data"][0]["embedding"] = [1]
    elif bad == "zero":
        body["data"][0]["embedding"] = [0, 0]
    elif bad in ("nan", "bool"):
        body["data"][0]["embedding"] = [float("nan") if bad == "nan" else True, 1]
    else:
        body["usage"]["prompt_tokens"] = True
    transport = httpx.MockTransport(lambda r: httpx.Response(200, content=json.dumps(body)))
    with Provider(settings(embedding_dimensions=2), transport) as p:
        with pytest.raises(ModelFailure, match="MODEL_RESPONSE_INVALID"):
            p.embed_texts(["a", "b"])


@pytest.mark.parametrize("texts", [[], [""], [" "], ["x"] * 11, ["x" * 6001], [True]])
def test_req601_invalid_input_never_sends_request(texts):
    def forbidden(request):
        pytest.fail("无效输入不应发送请求。")

    with Provider(settings(), httpx.MockTransport(forbidden)) as p:
        with pytest.raises(ValueError):
            p.embed_texts(texts)


def test_req601_extreme_finite_vectors_are_normalized_without_overflow():
    body = result()
    body["data"][0]["embedding"] = [1e-100, 0]
    body["data"][1]["embedding"] = [1e30, 0]
    with Provider(
        settings(embedding_dimensions=2),
        httpx.MockTransport(lambda r: httpx.Response(200, json=body)),
    ) as p:
        assert p.embed_texts(["a", "b"])["vectors"] == [[1, 0], [1, 0]]


def test_req601_oversized_integer_is_safe_model_failure():
    body = result()
    body["data"][0]["embedding"] = [10**1000, 1]
    with Provider(
        settings(embedding_dimensions=2),
        httpx.MockTransport(lambda r: httpx.Response(200, json=body)),
    ) as p:
        with pytest.raises(ModelFailure, match="MODEL_RESPONSE_INVALID"):
            p.embed_texts(["a", "b"])
