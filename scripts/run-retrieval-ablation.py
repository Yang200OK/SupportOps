"""四组按顺序实跑，模型失败停止且保存未执行组。"""

import json
import subprocess
import sys

from supportops.settings import ROOT


def main():
    output = ROOT / "docs/verification/phase-3-round-3"
    variants = [
        ("rrf", []),
        ("rrf-parent", ["--expand-parent"]),
        ("rrf-rerank", ["--rerank"]),
        ("rrf-rerank-parent", ["--rerank", "--expand-parent"]),
    ]
    ledger = output / "ablation-runs.json"
    if ledger.exists() or any((output / f"dev-{name}.json").exists() for name, _ in variants):
        raise RuntimeError("拒绝覆盖消融结果；重复实验应另建明确目录。")
    states = [
        {"variant": name, "status": "not_run", "report": f"dev-{name}.json"} for name, _ in variants
    ]
    ledger.write_text(json.dumps(states, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for state, (_, flags) in zip(states, variants, strict=True):
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/evaluate-retrieval.py"),
                "--dataset-dir",
                str(ROOT / "data/retrieval/baseline-v1"),
                "--split",
                "dev",
                "--mode",
                "rrf",
                "--output",
                str(output / state["report"]),
                *flags,
            ],
            cwd=ROOT,
            check=False,
        )
        state["status"] = "failed"
        if result.returncode == 0 and (output / state["report"]).exists():
            report = json.loads((output / state["report"]).read_text(encoding="utf-8"))
            state["summary"] = report["summary"]
            if report["summary"]["failed"] == 0 and report["summary"]["not_run"] == 0:
                state["status"] = "completed"
        ledger.write_text(json.dumps(states, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"消融组 {state['variant']}：{state['status']}", flush=True)
        if state["status"] != "completed":
            raise SystemExit(1)


if __name__ == "__main__":
    main()
