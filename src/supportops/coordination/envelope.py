"""新角色协议的无损来源信封，不改原工具或裁剪观测正文。"""

from copy import deepcopy

from supportops.investigations.live_sources import verify_snapshot


def normalize_live(value):
    verify_snapshot(value)
    result = deepcopy(value)
    snapshot = result["snapshot"]
    common = {
        k: v for k, v in snapshot.items() if k not in {"sha256", "content_sha256", "source_type"}
    }
    for item in result["evidence"]:
        source = item["source"]
        for key, expected in common.items():
            if key not in source or source[key] != expected:
                raise ValueError("LIVE_SNAPSHOT_INVALID")
            del source[key]
    return result


def expand_source(evidence, snapshot):
    return {
        **{k: v for k, v in snapshot.items() if k not in {"sha256", "content_sha256"}},
        **evidence["source"],
    }
