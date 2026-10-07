"""复现配置必须明确隔离，不允许覆盖既有凭据或选择备用端口。"""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def initializer():
    spec = importlib.util.spec_from_file_location(
        "reproduction_init", ROOT / "scripts/init-reproduction.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_req2201_new_configuration_is_independent_and_never_copies_old_keys(tmp_path):
    module = initializer()
    a = module.create(tmp_path, "clean-a", 18011, 15174)
    b = module.create(tmp_path, "clean-b", 18012, 15175)
    assert a["project"] != b["project"]
    one = (tmp_path / a["env_file"]).read_text(encoding="utf-8")
    two = (tmp_path / b["env_file"]).read_text(encoding="utf-8")
    assert one != two and "@postgres:5432/supportops_test" in one
    assert "SUPPORTOPS_MODEL_MAIN_MODEL=qwen3.6-plus" in one
    assert "SUPPORTOPS_MODEL_VISION_MODEL=qwen-vl-plus" in one
    assert "DASHSCOPE_API_KEY=" not in one and "SUPPORTOPS_MODEL_API_KEY=" not in one
    assert "SUPPORTOPS_REPRO_API_PORT=18011" in one
    with pytest.raises(FileExistsError):
        module.create(tmp_path, "clean-a", 18011, 15174)
    assert (tmp_path / a["env_file"]).read_text(encoding="utf-8") == one


@pytest.mark.parametrize(
    "name,api,web",
    [
        ("../escape", 18011, 15174),
        ("default", 8010, 15174),
        ("default", 18011, 5173),
        ("same", 18011, 18011),
        ("UPPER", 18011, 15174),
        ("bad", 65536, 15174),
    ],
)
def test_req2201_invalid_identity_or_ports_fail_before_writing(tmp_path, name, api, web):
    with pytest.raises(ValueError):
        initializer().create(tmp_path, name, api, web)
    assert list(tmp_path.iterdir()) == []


def test_req2203_offline_boundary_rejects_network_before_send(request, monkeypatch):
    if not request.config.getoption("--offline-models"):
        pytest.skip("仅由显式离线 CI 入口验证传输边界")
    import httpx

    from supportops.models.provider import ModelSettings, Provider

    sent = []

    def send(*args, **kwargs):
        sent.append(True)
        raise AssertionError("不应到达网络发送")

    monkeypatch.setattr(httpx.Client, "send", send)
    with Provider(ModelSettings(api_key="offline-contract-test-only", _env_file=None)) as provider:
        with pytest.raises(AssertionError, match="拒绝未显式模拟"):
            provider.post(provider.settings.base_url + "/chat/completions", {})
    assert sent == []
