"""包含本轮失败和旧浏览器回归的已观察用量，不估算未知账单。"""

import json

from supportops.settings import ROOT


def main():
    directory = ROOT / "docs/verification/phase-4-round-2"
    rows = []

    def add(source, case, usage):
        if usage is None:
            return
        rows.append(
            {
                "source": source,
                "case": case,
                "known_model_calls": usage.get(
                    "known_model_calls",
                    usage.get("model_calls", int(usage.get("model_called", True))),
                ),
                "unknown_usage_calls": usage.get("unknown_usage_calls", 0),
                "known_input_tokens": usage.get("input_tokens", 0) or 0,
                "known_output_tokens": usage.get("output_tokens", 0) or 0,
            }
        )

    for path in sorted(directory.glob("http*.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        for row in report["records"]:
            add(path.name, row["case"], row["response"].get("usage"))
    for path in sorted(directory.glob("live-*-attempt-*.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        if report.get("build_usage"):
            add(
                path.name,
                "isolated_fixture_embedding",
                {
                    "known_model_calls": report["build_model_calls"],
                    "input_tokens": report["build_usage"]["input_tokens"],
                },
            )
        else:
            add(
                path.name,
                "failed_fixture_embedding",
                {
                    "known_model_calls": 0,
                    "unknown_usage_calls": report.get("unknown_usage_calls", 0),
                },
            )
        add(path.name, "live_guided", report.get("response", {}).get("usage"))
    path = directory / "generation-probe.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    add(path.name, "probe_retrieval", report["retrieval_usage"])
    add(path.name, "probe_generation", report["usage"])
    if report.get("unknown_usage_calls"):
        add(
            path.name,
            "probe_unknown",
            {"known_model_calls": 0, "unknown_usage_calls": report["unknown_usage_calls"]},
        )
    for path in sorted(directory.rglob("*.jsonl")):
        if path.name not in {"browser-usage.jsonl", "browser-requests.jsonl"}:
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            add(
                str(path.relative_to(directory)),
                record.get("test", "real_browser"),
                record.get("usage"),
            )
    report = {
        "scope": "observed_http_live_test_fixtures_probe_and_browser_regression",
        "records": rows,
        "cost_cny": None,
        "billing_reconciled": False,
        "note": (
            "原失败、显式重测和隔离测试索引 embedding 均计入；"
            "页面协议截获不记为外部调用。连接 / 超时无服务商用量，仍列未知。"
            "诊断脚本首次本地登录 422 发生在模型调用前。"
        ),
    }
    for key in (
        "known_model_calls",
        "unknown_usage_calls",
        "known_input_tokens",
        "known_output_tokens",
    ):
        report[key] = sum(row[key] for row in rows)
    (directory / "model-usage.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {k: v for k, v in report.items() if k not in {"records", "note"}}, ensure_ascii=False
        )
    )


if __name__ == "__main__":
    main()
