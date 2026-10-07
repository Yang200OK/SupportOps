"""独立进程的非法启动也必须明确失败，不依赖实验追踪字段。"""

import json
import os
import subprocess
import sys
from uuid import UUID, uuid4

import pytest


@pytest.mark.parametrize(
    "config",
    [
        '{"product_version":"2.0","delivery_timeout_ms":2000}',
        '{"product_version":"3.0"}',
        "[]",
        "{broken",
    ],
)
def test_req502_invalid_cold_start_exits_with_original_code(config):
    env = {key: value for key, value in os.environ.items() if not key.startswith("LAB_")}
    env.update(RELAYDESK_CONFIG=config, PYTHONPATH="src", PYTHONUTF8="1")
    result = subprocess.run(
        [sys.executable, "-c", "import supportops.lab.product"],
        env=env,
        capture_output=True,
        encoding="utf-8",
        timeout=15,
    )
    assert result.returncode == 2
    assert json.loads(result.stdout.splitlines()[-1])["error_code"] == "RD_CONFIG_INVALID"


def test_req1304_opt_in_boot_identity_keeps_old_observation_contract():
    env = {key: value for key, value in os.environ.items() if not key.startswith("LAB_")}
    env.update(
        RELAYDESK_CONFIG='{"product_version":"1.1","downstream_timeout_ms":100}',
        LAB_RUN_ID=str(uuid4()),
        LAB_REQUEST_ID=str(uuid4()),
        LAB_DIAGNOSTIC_CONTEXT="1",
        PYTHONPATH="src",
        PYTHONUTF8="1",
    )
    result = subprocess.run(
        [sys.executable, "-c", "import supportops.lab.product"],
        env=env,
        capture_output=True,
        encoding="utf-8",
        timeout=15,
    )
    assert result.returncode == 2
    report = json.loads(result.stdout.splitlines()[-1])
    assert UUID(report["instance_id"])
    assert report["event"] == "config_checked" and report["error_code"] == "RD_CONFIG_INVALID"
