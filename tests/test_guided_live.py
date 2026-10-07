"""显式开启后运行真实模型和测试库；构造冲突不会污染开发冻结索引。"""

import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_documents_postgres import payload, post
from test_guided_postgres import two_documents
from test_ticket_postgres import context as context
from test_ticket_postgres import headers

from supportops.models.provider import Provider
from supportops.rag.contracts import AnswerDraft
from supportops.settings import ROOT

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("SUPPORTOPS_LIVE_GUIDED") != "1", reason="需要显式开启本轮真实模型验证"
    ),
]


def test_req1004_live_constructed_conflict_full_pipeline(context):
    output = Path(os.environ["SUPPORTOPS_LIVE_GUIDED_REPORT"]).resolve()
    if not output.is_relative_to(ROOT) or output.exists():
        raise ValueError("真实模型报告必须使用项目内的新路径。")
    auth = headers(context)
    record = two_documents(context, auth)
    response = context["client"].post(
        f"/api/retrieval/indexes/{record['index_id']}/guided-answer",
        headers=auth,
        json={
            "query": "RelayDesk 1.1 timeout_ms 默认值是多少？两份文档是否一致？",
            "product_version": "1.1",
            "mode": "bm25",
        },
    )
    body = response.json()
    report = {
        "transport": "real_testclient_postgres_real_bailian",
        "build_usage": record["build_usage"],
        "build_model_calls": len(record["build_usage"]["batches"]),
        "http_status": response.status_code,
        "response": body,
        "human_reviewed": False,
        "cost_cny": None,
        "cleanup": "only_fixture_organizations_in_supportops_test",
    }
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert response.status_code == 200
    assert body["status"] == "conflict" and body["answer"] is None
    assert len(body["conflicts"][0]["citations"]) >= 2
    assert all(c["literal_verified"] for c in body["conflicts"][0]["citations"])
    hits = {h["evidence_id"]: h for h in body["retrieval"]["items"]}
    for citation in body["conflicts"][0]["citations"]:
        detail = context["client"].get(hits[citation["evidence_id"]]["reference_url"], headers=auth)
        assert detail.status_code == 200 and detail.json()["text_verified"]
        assert citation["quote"] in detail.json()["chunk"]["text"]


def test_req1003_live_additional_retrieval_full_pipeline(context, monkeypatch):
    output = Path(os.environ["SUPPORTOPS_LIVE_GUIDED_ADDITIONAL_REPORT"]).resolve()
    if not output.is_relative_to(ROOT) or output.exists():
        raise ValueError("真实模型报告必须使用项目内的新路径。")
    auth = headers(context)
    sets = []
    for key, title, source in [
        ("alpha", "参数说明", "alpha_key用于控制发送超时。beta_key的定义需要查阅连接池说明。"),
        (
            "beta",
            "连接池说明",
            "beta_key用于控制连接池最大连接数。alpha_key的定义需要查阅参数说明。",
        ),
    ]:
        r = post(
            context,
            auth,
            payload((f"# {title}\n\n{source}\n").encode(), title=title, source_key=key),
        ).json()
        response = context["client"].post(
            f"/api/documents/{r['document_id']}/revisions/{r['revision_id']}/chunk-sets",
            headers=auth,
            json={},
        )
        assert response.status_code == 200
        sets.append(response.json()["chunk_set_id"])
    response = context["client"].post(
        "/api/retrieval/indexes", headers=auth, json={"chunk_set_ids": sets}
    )
    report = {
        "transport": "real_testclient_postgres_real_bailian",
        "index_status": response.status_code,
        "human_reviewed": False,
        "cost_cny": None,
    }
    record = response.json()
    if response.status_code == 200:
        report.update(
            build_usage=record["build_usage"],
            build_model_calls=len(record["build_usage"]["batches"]),
        )
    else:
        report.update(error_code=record.get("error", {}).get("code"), unknown_usage_calls=1)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert response.status_code == 200
    diagnostics = []
    original_post = Provider.post

    def inspect_contract(provider, url, body):
        result = original_post(provider, url, body)
        if body.get("model") == provider.settings.main_model:
            choice = result[0]["choices"][0]
            if "contexts" in json.loads(body["messages"][1]["content"]):
                row = {"finish_reason": choice["finish_reason"]}
                try:
                    AnswerDraft.model_validate_json(choice["message"]["content"])
                    row["schema"] = "passed"
                except ValidationError as exc:
                    row["schema_errors"] = exc.errors(
                        include_input=False, include_context=False, include_url=False
                    )
                diagnostics.append(row)
        return result

    monkeypatch.setattr(Provider, "post", inspect_contract)
    response = context["client"].post(
        f"/api/retrieval/indexes/{record['index_id']}/guided-answer",
        headers=auth,
        json={
            "query": "RelayDesk 1.1 alpha_key 与 beta_key 各有什么作用？请完整解释两个字段。",
            "product_version": "1.1",
            "mode": "bm25",
            "top_k": 1,
        },
    )
    body = response.json()
    report.update(
        http_status=response.status_code, response=body, generation_diagnostics=diagnostics
    )
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert response.status_code == 200
    assert body["status"] in ("answered", "limited_answer")
    assert len(body["retrieval"]["searches"]) == 2 and len(body["retrieval"]["items"]) == 2
    assert body["answer"] and body["answer"]["query"].startswith("RelayDesk 1.1 alpha_key")
