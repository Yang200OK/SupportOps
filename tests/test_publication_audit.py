"""公开提交检查必须阻止本机文件、缺失资源与密钥，不回显敏感值。"""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def auditor():
    spec = importlib.util.spec_from_file_location(
        "publication_audit", ROOT / "scripts/audit-publication.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        "local/demo-accounts.json",
        "docs/implementation-plan.md",
        "docs/specs/phase-8-round-3/spec.md",
        "AGENTS.md",
        "frontend/node_modules/a.js",
    ],
)
def test_req2303_private_candidate_rejected(tmp_path, path):
    with pytest.raises(ValueError, match="公开范围"):
        auditor().validate_paths([path])


def test_req2305_missing_readme_image_rejected(tmp_path):
    (tmp_path / "README.md").write_text("![资料](docs/images/missing.png)\n", encoding="utf-8")
    with pytest.raises(ValueError, match="资源"):
        auditor().validate_links(tmp_path, ["README.md"])


def test_req2303_actual_secret_rejected_without_echo(tmp_path):
    secret = "publication-sensitive-fixture-value"
    (tmp_path / "README.md").write_text(secret, encoding="utf-8")
    with pytest.raises(ValueError, match="敏感") as failure:
        auditor().validate_content(tmp_path, ["README.md"], {secret})
    assert secret not in str(failure.value)


def test_req2305_valid_relative_resource_and_empty_model_key(tmp_path):
    (tmp_path / "README.md").write_text("[说明](docs/setup/local-run.md)\n", encoding="utf-8")
    (tmp_path / "docs/setup").mkdir(parents=True)
    (tmp_path / "docs/setup/local-run.md").write_text("明确初始化自己的凭据。\n", encoding="utf-8")
    names = ["README.md", "docs/setup/local-run.md", ".env.example"]
    auditor().validate_paths(names)
    auditor().validate_links(tmp_path, names[:-1])
    auditor().validate_content(tmp_path, names[:-1], set())


def test_req2303_json_escaped_personal_path_rejected(tmp_path):
    import json

    private_path = "C:" + "\\" + "Users" + "\\" + "demo" + "\\" + "report.json"
    (tmp_path / "report.json").write_text(json.dumps({"path": private_path}), encoding="utf-8")
    with pytest.raises(ValueError, match="个人路径"):
        auditor().validate_content(tmp_path, ["report.json"], set())


def test_req2305_staged_readme_links_checked_from_staged_bytes(tmp_path):
    (tmp_path / "README.md").write_text("没有链接。", encoding="utf-8")
    with pytest.raises(ValueError, match="资源"):
        auditor().validate_links(
            tmp_path,
            ["README.md"],
            read=lambda name: b"![missing](missing.png)",
        )


def test_req2305_original_report_bytes_must_match_manifest(tmp_path):
    import hashlib
    import json

    (tmp_path / "docs/evidence").mkdir(parents=True)
    original = b'{"result": "failed"}\r\n'
    manifest = {
        "report_integrity": [
            {"path": "report.json", "sha256": hashlib.sha256(original).hexdigest()}
        ]
    }
    (tmp_path / "docs/evidence/source-manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    (tmp_path / "report.json").write_bytes(original.replace(b"\r\n", b"\n"))
    with pytest.raises(ValueError, match="摘要"):
        auditor().validate_integrity(
            tmp_path, ["docs/evidence/source-manifest.json", "report.json"]
        )
