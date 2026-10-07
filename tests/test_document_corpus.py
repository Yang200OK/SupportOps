"""发布样例必须与清单身份、字节和无评测答案契约一致。"""

import hashlib
import json
from pathlib import Path

from supportops.documents.parser import parse_document

ROOT = Path(__file__).resolve().parents[1] / "data" / "relaydesk"


def test_req301_corpus_manifest_and_all_formats_are_parseable():
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    sources = manifest["sources"]
    assert len(sources) == 16
    assert {s["product_version"] for s in sources} == {"1.0", "1.1", "2.0"}
    assert {s["format"] for s in sources} == {"md", "pdf", "json"}
    for source in sources:
        raw = (ROOT / source["path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == source["content_sha256"]
        assert source["runtime_verified"] is False
        assert source["license"] == "CC0-1.0"
        result = parse_document(raw, source["format"], source["product_version"])
        assert result.text and result.blocks
        if source["format"] == "pdf":
            assert result.blocks[0].page_number == 1
            assert "配置速查" in result.text and "downstream_timeout_ms" in result.text


def test_req301_version_configuration_difference_is_explicit():
    old = (ROOT / "1.0" / "configuration.md").read_text(encoding="utf-8")
    new = (ROOT / "2.0" / "configuration.md").read_text(encoding="utf-8")
    assert "delivery_timeout_ms=2000" in old
    assert "downstream_timeout_ms=3000" in new
    assert "delivery_timeout_ms" not in new
