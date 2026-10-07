"""补充环境验证只针对派发前失败，不能重放未知模型调用。"""

import hashlib
import importlib.util
from copy import deepcopy
from types import SimpleNamespace

import pytest

from supportops.chunks.chunking import digest
from supportops.evaluations.final import grid
from supportops.settings import ROOT


def test_req2105_environment_repair_rejects_any_possible_dispatch():
    spec = importlib.util.spec_from_file_location(
        "configuration_repair", ROOT / "scripts/verify-final-configuration.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    frozen = {"repeats": 2, "tasks": {"agents": [{"task_id": "prospective-configuration"}]}}
    original = {"attempts": grid(frozen, "agents")}
    for row in original["attempts"]:
        row.update(status="failed", error_code="HARNESS_AssertionError")
    assert len(module.preparation_failures(frozen, original)) == 6
    for key, value in [
        ("response", {}),
        ("lab_run_id", "registered"),
        ("initial_state_sha256", "prepared"),
        ("status", "request_started"),
    ]:
        changed = deepcopy(original)
        changed["attempts"][0][key] = value
        with pytest.raises(ValueError, match="派发"):
            module.preparation_failures(frozen, changed)


def test_req2105_new_model_cohort_rejects_source_or_model_drift(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "configuration_cohort", ROOT / "scripts/verify-final-configuration.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    path = tmp_path / "inference.py"
    path.write_text("frozen inference", encoding="utf-8")
    models = {"main_model": "qwen3.6-plus"}
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(
        module, "ModelSettings", lambda: SimpleNamespace(model_dump=lambda **kwargs: models)
    )
    monkeypatch.setattr(module, "AnswerModelSettings", lambda: SimpleNamespace(timeout_seconds=60))
    frozen = {
        "files": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()},
        "models": deepcopy(models),
        "answer_timeout_seconds": 60,
    }
    frozen["sha256"] = digest(frozen)
    module.check_cohort(frozen)
    path.write_text("changed inference", encoding="utf-8")
    with pytest.raises(ValueError, match="冻结变化"):
        module.check_cohort(frozen)
    path.write_text("frozen inference", encoding="utf-8")
    models["main_model"] = "other-model"
    with pytest.raises(ValueError, match="配置变化"):
        module.check_cohort(frozen)
