"""按实际新增请求汇总，续测继承结果不再次计算收费。"""

import json

from supportops.settings import ROOT


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    directory = ROOT / "docs/verification/phase-3-round-3"
    output = directory / "model-usage.json"
    if output.exists():
        raise RuntimeError("拒绝覆盖用量核对。")
    comparison = read(directory / "ablation-comparison.json")
    lost = comparison["lost_response_incident"]
    probe = read(directory / "rerank-probe.json")
    ann = read(directory / "vector-index.json")
    browser_rows = [
        json.loads(line)
        for path in (
            directory / "browser-usage.jsonl",
            directory / "previous-retrieval/browser-requests.jsonl",
        )
        for line in path.read_text("utf-8").splitlines()
    ]
    real_browser = [r for r in browser_rows if r.get("usage") is not None]
    protocol_failures = [r for r in browser_rows if r.get("usage") is None]
    assert len(protocol_failures) == 1 and protocol_failures[0]["status"] == 503
    assert protocol_failures[0]["request"]["mode"] == "bm25"
    browser_tokens = sum(r["usage"]["input_tokens"] for r in real_browser)
    browser_calls = sum(
        r["usage"].get("model_calls", int(r["usage"]["model_called"])) for r in real_browser
    )
    known = (
        comparison["known_input_tokens_including_failed_runs"]
        + probe["input_tokens"]
        + ann["known_input_tokens"]
        + browser_tokens
    )
    calls = comparison["actual_model_calls"] + 1 + ann["known_model_calls"] + browser_calls
    report = {
        "scope": "phase-3-round-3 only; previous index build excluded",
        "ablation_recorded_calls": comparison["actual_model_calls"],
        "ablation_known_tokens": comparison["known_input_tokens_including_failed_runs"],
        "probe_calls": 1,
        "probe_known_tokens": probe["input_tokens"],
        "ann_calls": ann["known_model_calls"],
        "ann_known_tokens": ann["known_input_tokens"],
        "browser_calls": browser_calls,
        "browser_known_tokens": browser_tokens,
        "browser_protocol_mock_failures": 1,
        "known_input_tokens": known,
        "model_call_attempts_min": calls + lost["total_model_calls_min"],
        "model_call_attempts_max": calls + lost["total_model_calls_max"],
        "unknown_usage_calls_min": comparison["unknown_usage_calls"]
        + lost["usage_unknown_calls_min"],
        "unknown_usage_calls_max": comparison["unknown_usage_calls"]
        + lost["usage_unknown_calls_max"],
        "lost_response_incident": lost,
        "cost_cny": None,
        "billing_reconciled": False,
    }
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
