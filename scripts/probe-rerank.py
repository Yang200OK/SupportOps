"""只验证本轮实际排序方法，使用固定公开构造输入。"""

import json
from datetime import datetime, timezone

from supportops.models.provider import ModelFailure, ModelSettings, Provider
from supportops.settings import ROOT


def main():
    output = ROOT / "docs/verification/phase-3-round-3/rerank-probe.json"
    if output.exists():
        raise RuntimeError("拒绝覆盖探测结果。")
    report = {"transport": "real_https", "at_utc": datetime.now(timezone.utc).isoformat()}
    try:
        settings = ModelSettings()
        with Provider(settings) as provider:
            report.update(
                provider.rerank_texts(
                    "RelayDesk RD_TIMEOUT 如何排查？",
                    [
                        "查看投递日志、实际超时配置与下游响应耗时。",
                        "可以调整页面颜色。",
                        "核对产品版本后补充证据。",
                    ],
                )
            )
        report["status"] = "passed"
    except ModelFailure as failure:
        report.update(status="failed", error_code=failure.code, http_status=failure.http_status)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
