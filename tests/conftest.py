"""集成测试必须显式开启，默认回归不隐式连接开发数据库。"""

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--integration", action="store_true", help="运行真实 supportops_test 数据库测试"
    )
    parser.addoption(
        "--offline-models", action="store_true", help="测试侧虚拟凭据，拒绝未模拟的模型传输"
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--integration"):
        return
    selected = []
    deselected = []
    for item in items:
        (deselected if "integration" in item.keywords else selected).append(item)
    items[:] = selected
    config.hook.pytest_deselected(items=deselected)


@pytest.fixture(autouse=True)
def no_implicit_development_database(monkeypatch):
    # 集成夹具之后显式设置测试库；其它测试不接触开发库。
    monkeypatch.setenv("SUPPORTOPS_DATABASE_URL", "")


@pytest.fixture(autouse=True)
def offline_model_boundary(request, monkeypatch):
    if not request.config.getoption("--offline-models"):
        return
    import httpx

    from supportops.models.provider import Provider

    # 旧协议测试曾隐式依赖开发机密钥。离线入口只给测试侧虚拟值，不能发送上游。
    monkeypatch.setenv("SUPPORTOPS_MODEL_API_KEY", "offline-contract-test-only")
    original = Provider.post

    def post(provider, url, payload):
        if not isinstance(provider.client._transport, httpx.MockTransport):
            raise AssertionError("离线测试拒绝未显式模拟的模型网络传输。")
        return original(provider, url, payload)

    monkeypatch.setattr(Provider, "post", post)
