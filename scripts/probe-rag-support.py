"""真实语义核对最小探测，不代替后续完整回答评测。"""

import argparse
import json
from pathlib import Path

from supportops.models.provider import ModelFailure, Provider
from supportops.rag.contracts import SupportReview
from supportops.rag.model import AnswerModelSettings, chat_json
from supportops.rag.service import REVIEW
from supportops.retrieval.contexts import text_hash
from supportops.settings import ROOT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT) or output.exists() or output.suffix != ".json":
        raise ValueError("报告需为项目内尚不存在的 JSON。")
    source = (ROOT / "data/relaydesk/1.1/configuration.md").read_text(encoding="utf-8")
    quote = "| delivery_timeout_ms | 3000 | ms | 下游请求超时 |"
    assert quote in source
    texts = [
        "RelayDesk 1.1 配置文档规定 delivery_timeout_ms 默认值是 3000，单位为 ms。",
        "RelayDesk 1.1 配置文档规定 delivery_timeout_ms 默认值是 30，单位为秒。",
        "当前客户故障已确认由实例实际配置 delivery_timeout_ms=3000 导致。",
    ]
    data = {
        "product_version": "1.1",
        "evidence_contexts": [
            {"context_id": "probe-configuration", "source_kind": "document", "text": source}
        ],
        "claims": [
            {
                "claim_id": f"C{n}",
                "kind": "fact",
                "text": text,
                "citations": [
                    {"context_id": "probe-configuration", "quote": quote, "source_kind": "document"}
                ],
            }
            for n, text in enumerate(texts, 1)
        ],
    }
    report = {
        "transport": "real_bailian_main_review",
        "scope": "three_contract_probes_only",
        "source_text_sha256": text_hash(source),
        "input": data,
        "status": "running",
        "human_reviewed": False,
        "cost_cny": None,
    }
    output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    save()
    try:
        with Provider(AnswerModelSettings()) as provider:
            result, usage = chat_json(provider, REVIEW, data, SupportReview, 2000)
        report.update(status="completed", result=result.model_dump(), usage=usage)
        by_id = {r.claim_id: r.verdict for r in result.items}
        report["contract_passed"] = set(by_id) == {"C1", "C2", "C3"} and (
            by_id["C1"] == "supported"
            and by_id["C2"] == "unsupported"
            and by_id["C3"] != "supported"
        )
        save()
        print(
            json.dumps(
                {"status": report["status"], "verdicts": by_id, "usage": usage}, ensure_ascii=False
            )
        )
        if not report["contract_passed"]:
            raise SystemExit(1)
    except ModelFailure as exc:
        report.update(
            status="failed",
            error_code=exc.code,
            usage=getattr(exc, "usage", None),
            unknown_usage_calls=int(
                getattr(exc, "usage", None) is None and exc.code != "MODEL_NOT_CONFIGURED"
            ),
        )
        save()
        raise SystemExit(exc.code) from None


if __name__ == "__main__":
    main()
