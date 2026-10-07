"""新步骤在旧检索失败时保留阶段，不假定未知收费为零。"""

import pytest

from supportops.api.errors import ServiceError
from supportops.retrieval import advanced
from supportops.retrieval.contracts import AdvancedSearch


def test_req803_retrieval_timeout_has_unknown_usage(monkeypatch):
    def fail(*args, **kwargs):
        raise ServiceError(503, "MODEL_TIMEOUT", "模型调用失败。")

    monkeypatch.setattr(advanced.hybrid, "search", fail)
    with pytest.raises(advanced.AdvancedFailure) as captured:
        advanced.search(
            None, None, None, AdvancedSearch(query="配置", product_version="1.0", rerank=True)
        )
    failure = captured.value
    assert failure.stage == "retrieval" and failure.usage["input_tokens"] is None
    assert failure.usage["unknown_usage_calls"] == 1
