"""用修正后的开发标签重新评分原响应，不调用模型或改变失败 / 未执行。"""

import argparse
import json
from pathlib import Path

from supportops.chunks.chunking import digest
from supportops.evaluations.answers import Dataset, Labels, Report, score
from supportops.settings import ROOT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--dataset-directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    paths = [args.report.resolve(), args.dataset_directory.resolve(), args.output.resolve()]
    if any(not p.is_relative_to(ROOT) for p in paths) or paths[2].exists():
        raise ValueError("只读项目输入并写新报告，禁止覆盖实跑结果。")
    dataset = Dataset.model_validate_json((paths[1] / "dataset.json").read_text(encoding="utf-8"))
    labels = Labels.model_validate_json((paths[1] / "labels.json").read_text(encoding="utf-8"))
    labels.bind(dataset)
    original = Report.model_validate_json(paths[0].read_text(encoding="utf-8"))
    if original.dataset_sha256 != digest(dataset.model_dump(mode="json")):
        raise ValueError("原响应的数据集不同，不能重新评分。")
    responses = paths[0].with_name(paths[0].stem + "-responses.json")
    raw = json.loads(responses.read_text(encoding="utf-8"))
    result = original.model_dump(mode="json")
    result["rescored_from_sha256"] = digest(result)
    result["labels_sha256"] = digest(labels.model_dump(mode="json"))
    for row in result["attempts"]:
        if row["status"] != "completed":
            continue
        task = next(t for t in dataset.tasks if t.task_id == row["task_id"])
        record = next(r for r in raw["records"] if r["case"] == row["task_id"])
        if record["status"] != 200 or record["request"] != task.request.model_dump(mode="json"):
            raise ValueError("原请求或状态不一致。")
        label = next(item for item in labels.labels if item.task_id == row["task_id"])
        row["metrics"] = score(record["response"], record["request"], label.model_dump(mode="json"))
    report = Report.model_validate(result)
    paths[2].write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report.summary(), ensure_ascii=False))


if __name__ == "__main__":
    main()
