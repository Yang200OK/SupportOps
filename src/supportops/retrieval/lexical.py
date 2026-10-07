"""有界语料的确定性词法排序，不读取标签或模型分数。"""

import math
import re
import unicodedata
from collections import Counter

TOKENIZER = "nfkc-ascii-cjk12-v1"
K1 = 1.2
B = 0.75
RRF_K = 60


def tokenize(text):
    normalized = unicodedata.normalize("NFKC", text).lower()
    tokens = []
    for part in re.findall(r"[a-z0-9][a-z0-9_.:-]*|[\u4e00-\u9fff]+", normalized):
        if "\u4e00" <= part[0] <= "\u9fff":
            tokens.extend(part)
            tokens.extend(part[i : i + 2] for i in range(len(part) - 1))
        else:
            tokens.append(part.rstrip("_.:-"))
    return tokens


def bm25(corpus, query):
    frequencies = {key: Counter(tokenize(text)) for key, text in corpus.items()}
    lengths = {key: sum(counts.values()) for key, counts in frequencies.items()}
    total = len(corpus)
    average = sum(lengths.values()) / total if total else 0
    if not average:
        return []
    terms = set(tokenize(query))
    df = Counter(term for counts in frequencies.values() for term in counts if term in terms)
    ranked = []
    for key, counts in frequencies.items():
        score = 0.0
        for term in sorted(terms):
            tf = counts[term]
            if tf:
                idf = math.log1p((total - df[term] + 0.5) / (df[term] + 0.5))
                score += idf * tf * (K1 + 1) / (tf + K1 * (1 - B + B * lengths[key] / average))
        if score > 0:
            ranked.append((key, score))
    return sorted(ranked, key=lambda item: (-item[1], item[0]))


def fuse(*rankings):
    scores = Counter()
    for ranking in rankings:
        if len(ranking) != len(set(ranking)):
            raise ValueError("单路排名不能重复证据。")
        for rank, key in enumerate(ranking, 1):
            scores[key] += 1 / (RRF_K + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))
