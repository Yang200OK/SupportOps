"""只累计真实原始尝试；重新评分报告不重复记作模型调用。"""

import json

from supportops.evaluations.answers import Report
from supportops.settings import ROOT


def main():
    directory = ROOT / "docs/verification/phase-4-round-3"
    rows = []

    def add(source, case, usage, default_calls=1):
        if usage is None:
            return
        rows.append(
            {
                "source": source,
                "case": case,
                "known_model_calls": usage.get(
                    "known_model_calls",
                    usage.get("model_calls", int(usage.get("model_called", bool(default_calls)))),
                ),
                "unknown_usage_calls": usage.get("unknown_usage_calls", 0),
                "known_input_tokens": usage.get("input_tokens", 0) or 0,
                "known_output_tokens": usage.get("output_tokens", 0) or 0,
            }
        )

    # 三份原实跑报告各只累计一次；对应 responses / corrected-rules 都不再次累计。
    for name in (
        "answer-report-attempt1.json",
        "answer-report-after-single-line.json",
        "answer-report-v2.json",
    ):
        report = Report.model_validate_json((directory / name).read_text(encoding="utf-8"))
        for row in report.attempts:
            add(name, row.task_id, row.usage.model_dump() if row.usage else None)
    for path in directory.glob("quote-diagnostic*.json"):
        body = json.loads(path.read_text(encoding="utf-8"))
        add(path.name, "retrieval", body["retrieval_usage"], 0)
        add(path.name, "generation", body["usage"])
        if body["unknown_usage_calls"]:
            add(
                path.name,
                "generation_unknown",
                {"known_model_calls": 0, "unknown_usage_calls": body["unknown_usage_calls"]},
            )
    for path in directory.glob("screenshots*.json"):
        for row in json.loads(path.read_text(encoding="utf-8"))["records"]:
            add(path.name, row["case"], row["response"].get("usage"))
    path = directory / "source-injection-attempt1.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    build = body["build_response"]["build_usage"]
    add(
        path.name,
        "isolated_fixture_embedding",
        {"known_model_calls": len(build["batches"]), "input_tokens": build["input_tokens"]},
    )
    for row in body["records"]:
        add(path.name, row["case"], row["response"].get("usage"))
    for path in directory.rglob("*.jsonl"):
        if path.name not in {"browser-live.jsonl", "browser-usage.jsonl", "browser-requests.jsonl"}:
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            add(
                str(path.relative_to(directory)),
                row.get("case", row.get("test", "browser")),
                row["response"].get("usage") if "response" in row else row.get("usage"),
            )
    value = {
        "scope": "current_round_observed_original_attempts_including_browser_regression",
        "records": rows,
        "cost_cny": None,
        "billing_reconciled": False,
        "note": (
            "原失败、显式新尝试、探测和隔离建库均计入。"
            "重评分与原文读回不调用模型；协议截获不计费用。"
        ),
    }
    for key in (
        "known_model_calls",
        "unknown_usage_calls",
        "known_input_tokens",
        "known_output_tokens",
    ):
        value[key] = sum(row[key] for row in rows)
    (directory / "model-usage.json").write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {k: v for k, v in value.items() if k not in {"records", "note"}}, ensure_ascii=False
        )
    )


if __name__ == "__main__":
    main()
