"""实际排序契约先失败，再按固定输入索引核对。"""

import json

import httpx
import pytest
from test_model_contract import settings

from supportops.models.provider import ModelFailure, Provider


def result(rows=None):
    return {
        "output": {
            "results": rows
            if rows is not None
            else [
                {"index": 2, "relevance_score": 0.9},
                {"index": 0, "relevance_score": 0.2},
                {"index": 1, "relevance_score": 0.2},
            ]
        },
        "usage": {"total_tokens": 123, "prompt_tokens": 123},
    }


def test_req801_real_text_rerank_request_preserves_all_indices_and_usage():
    calls = []

    def respond(request):
        calls.append(json.loads(request.content))
        assert request.url.path.endswith("/text-rerank/text-rerank")
        return httpx.Response(200, json=result())

    with Provider(settings(), transport=httpx.MockTransport(respond)) as provider:
        actual = provider.rerank_texts("RD_TIMEOUT", ["配置", "日志", "下游"])
    assert calls[0]["input"] == {"query": "RD_TIMEOUT", "documents": ["配置", "日志", "下游"]}
    assert calls[0]["parameters"]["top_n"] == 3
    assert actual["indices"] == [2, 0, 1] and actual["scores"] == [0.9, 0.2, 0.2]
    assert actual["input_tokens"] == 123 and actual["cost_cny"] is None


@pytest.mark.parametrize(
    "rows",
    [
        [{"index": 0, "relevance_score": 0.5}],
        [{"index": 0, "relevance_score": 0.5}] * 3,
        [
            {"index": True, "relevance_score": 0.5},
            {"index": 1, "relevance_score": 0.5},
            {"index": 2, "relevance_score": 0.5},
        ],
        [
            {"index": 0, "relevance_score": float("nan")},
            {"index": 1, "relevance_score": 0.5},
            {"index": 2, "relevance_score": 0.5},
        ],
        [
            {"index": 0, "relevance_score": True},
            {"index": 1, "relevance_score": 0.5},
            {"index": 2, "relevance_score": 0.5},
        ],
        [
            {"index": 0, "relevance_score": 1.1},
            {"index": 1, "relevance_score": 0.5},
            {"index": 2, "relevance_score": 0.5},
        ],
    ],
)
def test_req801_invalid_indices_and_scores_fail(rows):
    with Provider(
        settings(),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, content=json.dumps(result(rows)))
        ),
    ) as provider:
        with pytest.raises(ModelFailure, match="MODEL_RESPONSE_INVALID"):
            provider.rerank_texts("query", ["a", "b", "c"])


@pytest.mark.parametrize(
    "query,texts",
    [("", ["a"]), ("q", []), ("q", ["x"] * 41), ("q", ["x" * 6001]), ("q", ["x" * 6000] * 11)],
)
def test_req801_bounded_input_is_checked_before_network(query, texts):
    calls = []
    with Provider(settings(), transport=httpx.MockTransport(lambda r: calls.append(r))) as provider:
        with pytest.raises(ValueError):
            provider.rerank_texts(query, texts)
    assert not calls
