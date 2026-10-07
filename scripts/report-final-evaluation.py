"""从原始全网格生成结果 / 失败分析，或零模型读回全部引用与持久化记录。"""

import argparse
import json

import httpx

from supportops.chunks.chunking import digest
from supportops.coordination.comparison import PROTOCOL_DIFFERENCES
from supportops.evaluations.final import (
    VARIANTS,
    bind_manifest,
    paired_conditions,
    summarize,
    validate_report,
)
from supportops.settings import ROOT

DIRECTORY = ROOT / "docs/verification/phase-8-round-1"


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    if path.exists():
        raise ValueError("不覆盖结果或读回证据。")
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def readback(manifest, reports, label):
    result = {
        "manifest_sha256": manifest["sha256"],
        "model_calls": 0,
        "records": {},
        "references": {},
        "cross_organization_404": True,
    }
    accounts = load(ROOT / "local/demo-accounts.json")["accounts"]
    with httpx.Client(base_url="http://127.0.0.1:8010", timeout=30, trust_env=False) as client:
        auth = []
        try:
            for name in ("support_a", "support_b"):
                account = next(a for a in accounts if a["username"] == name)
                response = client.post(
                    "/api/auth/login", json={"username": name, "password": account["password"]}
                )
                response.raise_for_status()
                auth.append({"Authorization": "Bearer " + response.json()["access_token"]})

            def get(path):
                response = client.get(path, headers=auth[0])
                response.raise_for_status()
                return response.json()

            index_path = f"/api/retrieval/indexes/{manifest['index']['index_id']}"
            assert get(index_path) == manifest["index"]
            assert client.get(index_path, headers=auth[1]).status_code == 404
            entries = []
            for offset in range(0, 502, 100):
                entries.extend(get(index_path + f"/entries?offset={offset}&limit=100")["items"])
            assert len(entries) == 502 and digest(entries) == manifest["entries_sha256"]
            result.update(entries=502, entries_sha256=digest(entries))
            rows = [row for r in reports.values() for row in r["attempts"]]
            old = load(ROOT / "docs/verification/phase-7-round-3/pairs-attempt1.json")
            for item in old["attempts"]:
                body = item["response"]
                path = (
                    f"/api/investigations/{body['investigation_id']}"
                    if item["arm"] == "single"
                    else f"/api/coordination-executions/{body['execution_id']}"
                )
                assert get(path) == body
                result["records"][path] = digest(body)
            for row in rows:
                if row.get("record_path"):
                    assert get(row["record_path"]) == row["response"]
                    assert client.get(row["record_path"], headers=auth[1]).status_code == 404
                    result["records"][row["record_path"]] = digest(row["response"])
                for path, sha in (row.get("references") or {}).items():
                    assert digest(get(path)) == sha
                    assert client.get(path, headers=auth[1]).status_code == 404
                    result["references"][path] = sha
            for task in manifest["tasks"]["agents"]:
                path = (
                    f"/api/tickets/{task['ticket']['ticket_id']}/memory-recall?mode={task['mode']}"
                )
                assert get(path) == task["memory"]
            result["old_development_records_unchanged"] = len(old["attempts"])
        finally:
            for headers in auth:
                client.post("/api/auth/logout", headers=headers).raise_for_status()
    write(DIRECTORY / f"readback-{label}.json", result)
    print(
        json.dumps(
            {
                "records": len(result["records"]),
                "references": len(result["references"]),
                "entries": 502,
                "model_calls": 0,
            },
            ensure_ascii=False,
        )
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--readback", choices=("before-restart", "after-restart"))
    args = parser.parse_args()
    manifest = load(ROOT / "data/evaluations/final-v1/manifest.json")
    bind_manifest(manifest)
    reports = {suite: load(DIRECTORY / (suite + ".json")) for suite in VARIANTS}
    for report in reports.values():
        validate_report(manifest, report)
    if args.readback:
        readback(manifest, reports, args.readback)
        return
    result = {
        "manifest_sha256": manifest["sha256"],
        "reports_sha256": {suite: digest(r) for suite, r in reports.items()},
        "suites": {},
        "pairs": [],
        "failures": [],
        "protocol_differences": PROTOCOL_DIFFERENCES,
        "human_gold_accuracy": None,
        "cost_cny": None,
    }
    text = [
        "# 最终冻结评测",
        "",
        "全部原始尝试与两次重复均保留；未调参、未择优或自动重试。",
        "初稿规则与模型评审不代表人工准确率，费用未对账。",
        "",
    ]
    for suite, report in reports.items():
        rows = report["attempts"]
        groups = {}
        text.extend(
            [
                f"## {suite}",
                "",
                "| 组 | 完成 / 全分母 | 已知 / 未知模型 | 输入 / 输出 token | P95 ms（全部尝试） |",
                "|---|---:|---:|---:|---:|",
            ]
        )
        for variant in VARIANTS[suite]:
            chosen = [r for r in rows if r["variant"] == variant]
            group = summarize(chosen)
            group["repeats"] = {
                str(n): summarize([r for r in chosen if r["repeat"] == n]) for n in (1, 2)
            }
            if suite == "agents":
                # 失败臂的已取得现场证据也计入，不能只统计成功子集。
                measured = [r["agent_metrics"] for r in chosen if r.get("agent_metrics")]
                group["current_evidence_all_attempts"] = sum(
                    m["current_evidence"] for m in measured
                )
                group["claim_count_all"] = sum(m["claim_count"] for m in measured)
                group["model_not_supported_all"] = sum(
                    m["review_not_supported"] or 0 for m in measured
                )
            groups[variant] = group
            text.append(
                f"| {variant} | {group['completed']} / {group['total']} "
                f"| {group['known_calls']} / {group['unknown_calls']} "
                f"| {group['input_tokens']} / {group['output_tokens']} "
                f"| {group['p95_ms_all_attempts']} |"
            )
        result["suites"][suite] = {"all": summarize(rows), "groups": groups}
        text.extend(
            [
                "",
                "各组原始全分母代理指标：",
                "",
                "```json",
                json.dumps(
                    {k: v["quality_all"] for k, v in groups.items()}, ensure_ascii=False, indent=2
                ),
                "```",
                "",
            ]
        )
        contrasts = {
            "retrieval": [
                ("rrf", "rrf-parent"),
                ("rrf", "rrf-rerank"),
                ("rrf-rerank", "rrf-rerank-parent"),
            ],
            "answers": [("direct", "guided")],
            "agents": [("single", "memory"), ("single", "multi")],
        }[suite]
        for task in manifest["tasks"][suite]:
            for repeat in (1, 2):
                for left_name, right_name in contrasts:
                    left = next(
                        r
                        for r in rows
                        if (r["task_id"], r["repeat"], r["variant"])
                        == (task["task_id"], repeat, left_name)
                    )
                    right = next(
                        r
                        for r in rows
                        if (r["task_id"], r["repeat"], r["variant"])
                        == (task["task_id"], repeat, right_name)
                    )
                    result["pairs"].append(
                        {
                            "suite": suite,
                            "task_id": task["task_id"],
                            "repeat": repeat,
                            "left": left_name,
                            "right": right_name,
                            **paired_conditions(left, right),
                            "left_status": left["status"],
                            "right_status": right["status"],
                            "both_completed": left["status"] == right["status"] == "completed",
                            "isolated_topology_comparison": False,
                        }
                    )
        for row in rows:
            if (
                row["status"] != "completed"
                or any(
                    (row.get("quality") or {}).get(k, 1) < 1
                    for k in ("recall", "rule_fact_coverage", "outcome_match")
                    if (row.get("quality") or {}).get(k) is not None
                )
                or (row.get("agent_metrics") or {}).get("review_not_supported")
            ):
                result["failures"].append(
                    {
                        "suite": suite,
                        **{
                            k: row.get(k)
                            for k in (
                                "task_id",
                                "repeat",
                                "variant",
                                "status",
                                "error_code",
                                "failed_stage",
                                "quality",
                                "agent_metrics",
                                "failure_type",
                            )
                        },
                    }
                )
    all_rows = [r for report in reports.values() for r in report["attempts"]]
    result["model_usage_all"] = {
        k: summarize(all_rows)[k]
        for k in (
            "known_calls",
            "unknown_calls",
            "input_tokens",
            "output_tokens",
            "unknown_pipeline_attempts",
            "cost_cny",
        )
    }
    text.extend(
        [
            "## 条件、分母与限制",
            "",
            *["- " + b for b in manifest["boundaries"]],
            "- direct / guided 是完整流程对照，规划和检索次数不同；不是单一改写变量消融。",
            "- single / memory 的记忆占用原上下文预算；single / multi 保留检索与角色协议差异。",
            "- 时延含网络、排队和失败早停。两次重复用于展示波动，不证明显著性或生产性能。",
            "- 新旧实际读回、后端测试和浏览器证据另见验收记录。",
            "",
            f"共 {len(all_rows)} 槽位，条件匹配 {sum(p['matched'] for p in result['pairs'])} "
            f"/ {len(result['pairs'])} 对；完整失败 / 低覆盖记录见 summary.json。",
            "",
        ]
    )
    write(DIRECTORY / "summary.json", result)
    md = DIRECTORY / "results.md"
    if md.exists():
        raise ValueError("不覆盖最终结果。")
    md.write_text("\n".join(text), encoding="utf-8")
    print(json.dumps(result["model_usage_all"], ensure_ascii=False))


if __name__ == "__main__":
    main()
