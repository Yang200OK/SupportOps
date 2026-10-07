"""汇总本轮已落盘模型用量；费用与失败未知用量不补造。"""

import json

from supportops.settings import ROOT


def main():
    directory = ROOT / "docs/verification/phase-4-round-1"
    rows = []

    def add(source, label, usage):
        if not usage:
            return
        calls = usage.get(
            "known_model_calls", usage.get("model_calls", int(usage.get("model_called", True)))
        )
        rows.append(
            {
                "source": source,
                "case": label,
                "known_model_calls": calls,
                "unknown_usage_calls": usage.get("unknown_usage_calls", 0),
                "known_input_tokens": usage.get("input_tokens", 0) or 0,
                "known_output_tokens": usage.get("output_tokens", 0) or 0,
            }
        )

    for filename in ("http-attempt-2.json", "http-attempt-3.json"):
        report = json.loads((directory / filename).read_text(encoding="utf-8"))
        for row in report["records"]:
            add(filename, row["case"], row["response"].get("usage"))
    for filename in ("support-probe.json", "support-probe-attempt-2.json"):
        report = json.loads((directory / filename).read_text(encoding="utf-8"))
        add(filename, "support_probe", report.get("usage"))
        if report.get("unknown_usage_calls"):
            add(
                filename,
                report["error_code"],
                {"known_model_calls": 0, "unknown_usage_calls": report["unknown_usage_calls"]},
            )
    for filename in (
        "browser-usage.jsonl",
        "regression/phase-3-round-2/browser-requests.jsonl",
        "regression/phase-3-round-3/browser-usage.jsonl",
    ):
        for line in (directory / filename).read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            add(filename, row.get("test", "browser"), row.get("usage"))
    report = {
        "scope": "observed_new_answer_and_regression_records",
        "records": rows,
        "cost_cny": None,
        "billing_reconciled": False,
        "note": (
            "连接失败尝试和读取超时没有服务商用量，仍列为未知；"
            "界面截获的伪造 503 不算外部模型调用。"
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
