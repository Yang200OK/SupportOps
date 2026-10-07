"""保留 Docker 输出的原始 JSONL 字节，排除访问日志与实验控制记录。"""

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

from supportops.settings import ROOT

DOCKER = os.environ.get("SUPPORTOPS_DOCKER_EXECUTABLE", "docker")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT):
        raise ValueError("日志必须保存在项目内。")
    output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    wanted = {r["run_id"] for r in manifest["records"]}
    report = []
    for service in ("relaydesk", "receiver"):
        name = f"supportops-lab-{service}-1"
        identity = (
            subprocess.run(
                [
                    DOCKER,
                    "inspect",
                    "--format",
                    '{{index .Config.Labels "com.docker.compose.project"}} {{.Image}}',
                    name,
                ],
                capture_output=True,
                check=True,
                encoding="utf-8",
            )
            .stdout.strip()
            .split()
        )
        if identity[0] != "supportops-lab":
            raise ValueError("拒绝读取其它 Docker 项目日志。")
        result = subprocess.run([DOCKER, "logs", name], capture_output=True, check=True)
        lines = []
        for line in result.stdout.splitlines(keepends=True):
            if line.startswith(b"{") and json.loads(line)["run_id"] in wanted:
                lines.append(line)
        raw = b"".join(lines)
        assert lines, "缺少与本次实验对应的真实结构化日志。"
        filename = f"raw-{service}.jsonl"
        (output / filename).write_bytes(raw)
        report.append(
            {
                "service": service,
                "container": name,
                "image_id": identity[1],
                "path": filename,
                "records": len(lines),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    (output / "raw-log-manifest.json").write_text(
        json.dumps(
            {"source": "docker_stdout_json_lines", "preserved_bytes": True, "records": report},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print("两个实验服务的原始结构化 JSONL 字节已保存。")


if __name__ == "__main__":
    main()
