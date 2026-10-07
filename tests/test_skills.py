"""项目 Skill 的适用条件、渐进加载与失败停止测试。"""

import json
import shutil

import pytest

from supportops.api.errors import ServiceError
from supportops.settings import ROOT
from supportops.skills.catalog import Catalog, sha256


def test_req1501_catalog_has_metadata_only_and_does_not_read_bodies(tmp_path):
    shutil.copytree(ROOT / "skills", tmp_path / "skills")
    for path in (tmp_path / "skills").rglob("SKILL.md"):
        path.unlink()
    rows = Catalog(tmp_path).listing()
    assert len(rows["items"]) == 4
    assert all("body" not in row for row in rows["items"])
    assert all(row["version"] == "1.0.0" for row in rows["items"])


@pytest.mark.parametrize("version", ["1.0", "1.1", "2.0"])
def test_req1502_versions_and_modes(version):
    catalog = Catalog()
    bundle = catalog.prepare(version, "online", "RD_POOL_WAIT 连接池等待")
    assert [s["skill_id"] for s in bundle["loaded"]] == ["pool-investigation"]
    assert bundle["loaded"][0]["body_sha256"] == sha256(bundle["loaded"][0]["body"].encode("utf-8"))
    assert catalog.prepare(version, "startup", "RD_POOL_WAIT")["loaded"] == []
    assert catalog.prepare(version, "online", "无症状信息")["loaded"] == []


def test_req1502_selection_is_bounded_and_explained():
    catalog = Catalog()
    text = "RD_TIMEOUT RD_POOL_WAIT RD_CACHE_STALE RD_CONFIG_INVALID"
    a = catalog.prepare("1.1", "online", text)
    assert a == catalog.prepare("1.1", "online", text)
    assert len(a["loaded"]) == 2
    assert all(s["matched_signals"] for s in a["loaded"])
    assert a["selection_limited"] is True
    assert all("body" not in s for s in a["catalog"]["items"])


@pytest.fixture
def copied(tmp_path):
    shutil.copytree(ROOT / "skills", tmp_path / "skills")
    shutil.copytree(ROOT / "data/relaydesk", tmp_path / "data/relaydesk")
    return tmp_path


def test_req1503_body_drift_stops(copied):
    target = copied / "skills/pool-investigation/1.0.0/SKILL.md"
    target.write_text("修改后的方法", encoding="utf-8")
    with pytest.raises(ServiceError, match="正文"):
        Catalog(copied).prepare("1.1", "online", "RD_POOL_WAIT")


def test_req1503_source_drift_stops(copied):
    target = copied / "data/relaydesk/1.1/troubleshooting.md"
    target.write_text("改变的原始来源", encoding="utf-8")
    with pytest.raises(ServiceError, match="来源"):
        Catalog(copied).prepare("1.1", "online", "RD_POOL_WAIT")


def test_req1501_catalog_drift_and_permission_fields_are_rejected(copied):
    path = copied / "skills/catalog.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["items"][0]["permissions"] = ["shell"]
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ServiceError):
        Catalog(copied).listing()


def test_req1503_wrong_mode_and_version_never_load():
    catalog = Catalog()
    with pytest.raises(ServiceError):
        catalog.detail("pool-investigation", "1.0.0", "1.1", "startup")
    with pytest.raises(ServiceError):
        catalog.detail("pool-investigation", "9.0.0", "1.1", "online")
    with pytest.raises(ServiceError):
        catalog.prepare("9.0", "online", "RD_POOL_WAIT")


def test_req1504_request_has_explicit_switch_and_rejects_permissions():
    from uuid import uuid4

    from pydantic import ValidationError

    from supportops.investigations.hypothesis_contracts import HypothesisRequest

    assert HypothesisRequest(index_id=uuid4()).use_skills is False
    with pytest.raises(ValidationError):
        HypothesisRequest(index_id=uuid4(), use_skills=True, tools=["shell"])


def test_req1503_missing_and_invalid_utf8_stop(copied):
    path = copied / "skills/pool-investigation/1.0.0/SKILL.md"
    path.unlink()
    with pytest.raises(ServiceError):
        Catalog(copied).prepare("1.1", "online", "连接池")
    path.write_bytes(b"\xff\xfe")
    with pytest.raises(ServiceError):
        Catalog(copied).prepare("1.1", "online", "连接池")


def test_req1503_unmatched_body_is_not_loaded(copied):
    path = copied / "skills/cache-investigation/1.0.0/SKILL.md"
    path.unlink()
    assert len(Catalog(copied).prepare("1.1", "online", "连接池")["loaded"]) == 1


@pytest.mark.parametrize("mutation", ["permissions", "duplicate", "source_path", "source_version"])
def test_req1501_schema_rejects_even_republished_invalid_metadata(copied, monkeypatch, mutation):
    import supportops.skills.catalog as module

    path = copied / "skills/catalog.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    if mutation == "permissions":
        raw["items"][0]["permissions"] = ["shell"]
    elif mutation == "duplicate":
        raw["items"].append(raw["items"][0])
    elif mutation == "source_path":
        raw["items"][0]["sources"][0]["path"] = "../../.env"
    else:
        raw["items"][0]["sources"][0]["product_version"] = "2.0"
    data = json.dumps(raw).encode("utf-8")
    path.write_bytes(data)
    # 只模拟维护者重新发布后的契约校验，生产仍必须先匹配发布哈希。
    monkeypatch.setattr(module, "CATALOG_SHA256", sha256(data))
    with pytest.raises(ServiceError) as error:
        Catalog(copied)
    assert error.value.code == "SKILL_CATALOG_INVALID"


def test_req1503_path_escape_and_oversized_body_stop(copied):
    catalog = Catalog(copied)
    (copied.parent / "outside.md").write_text("测试根目录外的文件", encoding="utf-8")
    with pytest.raises(ServiceError):
        catalog._read("../outside.md", 100, "SKILL_BODY_INVALID", "正文")
    target = copied / "skills/pool-investigation/1.0.0/SKILL.md"
    target.write_bytes(b"a" * 8193)
    with pytest.raises(ServiceError) as error:
        catalog.detail("pool-investigation", "1.0.0", "1.1", "online")
    assert error.value.code == "SKILL_BODY_INVALID"


def test_req1504_two_bodies_obey_the_context_cap(monkeypatch):
    import supportops.skills.catalog as module

    monkeypatch.setattr(module, "MAX_CONTEXT_CHARS", 10)
    with pytest.raises(ServiceError) as error:
        Catalog().prepare("1.1", "online", "连接池")
    assert error.value.code == "SKILL_CONTEXT_LIMIT"
