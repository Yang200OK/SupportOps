"""只读审计待建库公开资料，不联网，不输出凭据。"""

import hashlib
import json

from supportops.lab.contracts import LabBundle
from supportops.settings import ROOT


def main():
    manifest = json.loads((ROOT / "data/relaydesk/manifest.json").read_text(encoding="utf-8"))
    sources = manifest["sources"]
    for source in sources:
        assert source["license"] == "CC0-1.0"
        assert source["origin"] == "self_authored_demo_design"
        assert source["source_type"] in ("demo_product", "synthetic_case")
        assert (
            hashlib.sha256((ROOT / "data/relaydesk" / source["path"]).read_bytes()).hexdigest()
            == source["content_sha256"]
        )
    lab = json.loads((ROOT / "data/lab/manifest.json").read_text(encoding="utf-8"))
    observations = 0
    for record in lab["records"]:
        bundle = LabBundle.model_validate_json(
            (ROOT / "data/lab" / record["path"]).read_text(encoding="utf-8")
        )
        assert bundle.digest() == record["sha256"]
        observations += len(bundle.observations)
    report = {
        "documents": len(sources),
        "document_license": "CC0-1.0",
        "document_origin": "self_authored_demo_design",
        "real_user_reports": 0,
        "public_lab_bundles": len(lab["records"]),
        "strict_public_observations": observations,
        "lab_data_kind": "self_authored_local_docker_experiment",
        "label_or_control_files_in_embedding": False,
        "destination": "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings",
        "network_requests_in_audit": 0,
    }
    path = ROOT / "docs/verification/phase-3-round-1/input-audit.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
