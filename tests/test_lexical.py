"""词法边界、手算分数与融合排名，先验证缺失实现。"""

import math

import pytest

from supportops.retrieval.lexical import bm25, fuse, tokenize


def test_req701_chinese_identifiers_and_normalization():
    assert tokenize("ＲＤ＿ＴＩＭＥＯＵＴ timeout_ms / Pool.Timeout 连接池，超时") == [
        "rd_timeout",
        "timeout_ms",
        "pool.timeout",
        "连",
        "接",
        "池",
        "连接",
        "接池",
        "超",
        "时",
        "超时",
    ]
    assert tokenize("连接，池") != tokenize("连接池")


def test_req702_hand_computed_bm25_and_query_dedup():
    corpus = {"b": "code code", "a": "other", "c": "code"}
    hits = bm25(corpus, "code code")
    assert [h[0] for h in hits] == ["b", "c"]
    expected = math.log1p(1.5 / 2.5) * 2 * 2.2 / (2 + 1.2 * (0.25 + 0.75 * 2 / (4 / 3)))
    assert hits[0][1] == pytest.approx(expected)
    assert bm25(corpus, "code") == hits
    assert bm25(corpus, "unseen") == []
    assert bm25({}, "code") == []


def test_req703_rrf_uses_ranks_deduplicates_and_breaks_ties():
    hits = fuse(["a", "b"], ["b", "a", "c"])
    assert [h[0] for h in hits] == ["a", "b", "c"]
    assert hits[0][1] == pytest.approx(1 / 61 + 1 / 62)
    assert hits[2][1] == pytest.approx(1 / 63)
    assert fuse([], ["a"]) == [("a", 1 / 61)]
    with pytest.raises(ValueError):
        fuse(["a", "a"], [])
