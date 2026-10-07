"""只发送固定构造输入，真实验证四种独立模型契约。"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from supportops.models.provider import ModelFailure, ModelSettings, Provider


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("outputs/model-probe.json"))
    args = parser.parse_args()
    config = ModelSettings()
    report = {
        "executed_at_utc": datetime.now(timezone.utc).isoformat(),
        "transport": "real_https",
        "input_kind": "fixed_synthetic_probe",
        "base_url": config.base_url,
        "rerank_url": config.rerank_url,
        "checks": [],
        "quality_evaluation": "not_run",
    }
    try:
        with Provider(config) as provider:
            for name, call in (
                ("main", lambda: provider.chat("main")),
                ("light", lambda: provider.chat("light")),
                ("embedding", provider.embedding),
                ("rerank", provider.rerank),
            ):
                try:
                    result = call()
                    report["checks"].append({"role": name, "status": "passed", **result})
                    print(f"模型契约通过：{name}，输入 token={result['input_tokens']}。")
                except ModelFailure as exc:
                    report["checks"].append(
                        {
                            "role": name,
                            "status": "failed",
                            "error_code": exc.code,
                            "http_status": exc.http_status,
                        }
                    )
                    raise
    except ModelFailure as exc:
        report["status"] = "failed"
        print(f"模型验证失败：{exc.code}；HTTP 状态={exc.http_status}。没有重试或切换模型。")
    else:
        report["status"] = "passed"
    finally:
        recorded = {item["role"] for item in report["checks"]}
        report["checks"].extend(
            {"role": role, "status": "not_run"}
            for role in ("main", "light", "embedding", "rerank")
            if role not in recorded
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
