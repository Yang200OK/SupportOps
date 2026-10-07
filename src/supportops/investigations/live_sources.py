"""只读适配固定实验地址；来源身份变化显式失败，不读取其它日志。"""

import json
from datetime import datetime, timezone
from uuid import UUID

import httpx
from dotenv import dotenv_values
from pydantic import Field, ValidationError

from supportops.api.errors import ServiceError
from supportops.chunks.chunking import digest
from supportops.lab.contracts import Observation, RelayConfig, Strict, Target
from supportops.settings import ROOT


class ProductState(Strict):
    service: str
    instance_id: UUID
    config: RelayConfig
    checked_out: int = Field(ge=0, le=10)


class ReceiverState(Strict):
    service: str
    instance_id: UUID
    active_target: Target
    delay_ms: int = Field(ge=0, le=5000)


def fail(code, message):
    return ServiceError(409, code, message)


def lab_client():
    token = dotenv_values(ROOT / ".env.lab").get("LAB_CONTROL_TOKEN")
    if not token:
        raise fail("LIVE_SOURCE_UNAVAILABLE", "固定实验读取凭据未配置。")
    # 令牌只在此固定 GET 适配层使用，不返回给 MCP 客户端或模型。
    return httpx.Client(
        headers={"X-Lab-Control": token}, timeout=5, trust_env=False, follow_redirects=False
    )


def fetch(client, port, path):
    try:
        response = client.get(f"http://127.0.0.1:{port}{path}")
        response.raise_for_status()
        if len(response.content) > 64000:
            raise fail("LIVE_SOURCE_INVALID", "本次观测返回超过固定上限。")
        return response.json()
    except (httpx.HTTPError, ValueError):
        raise fail("LIVE_SOURCE_UNAVAILABLE", "固定实验只读接口不可用或响应无效。") from None


def states(client, binding):
    product = ProductState.model_validate(fetch(client, 8101, "/diagnostics/state"))
    receiver = ReceiverState.model_validate(fetch(client, 8102, "/diagnostics/state"))
    if (
        product.service != "relaydesk"
        or receiver.service != "receiver"
        or product.instance_id != binding.instance_id
        or receiver.instance_id != binding.receiver_instance_id
    ):
        raise fail("LIVE_INSTANCE_CHANGED", "绑定实验实例已变化，需重新登记。")
    if product.config.product_version != binding.product_version:
        raise fail("INVESTIGATION_VERSION_MISMATCH", "实际实验版本与登记不符。")
    return [product.model_dump(mode="json"), receiver.model_dump(mode="json")]


def verify_snapshot(value):
    snapshot = value["snapshot"]
    material = {k: v for k, v in snapshot.items() if k not in ("sha256", "content_sha256")}
    try:
        data = [json.loads(e["text"]) for e in value["evidence"]]
        actual = digest({**material, "data": data})
        content = digest(data)
    except (ValueError, KeyError):
        raise fail("LIVE_SNAPSHOT_INVALID", "观测快照摘要无法核对。") from None
    if actual != snapshot["sha256"] or content != snapshot["content_sha256"]:
        raise fail("LIVE_SNAPSHOT_INVALID", "观测快照摘要不一致。")
    return data


def capture(binding, lab_run_id, investigation_id, name, client=None):
    now = datetime.now(timezone.utc)
    if not binding.started_at <= now < binding.expires_at:
        raise fail("LIVE_RUN_EXPIRED", "本次实验登记已过期或尚未开始。")
    data = []
    try:
        if name == "read_startup_diagnostic":
            if binding.mode != "startup":
                raise fail("LIVE_TOOL_MODE_INVALID", "在线运行没有绑定启动诊断。")
            data = [binding.boot.model_dump(mode="json")]
            origin = "current_startup_diagnostic"
        else:
            if binding.mode != "online":
                raise fail("LIVE_TOOL_MODE_INVALID", "启动失败场景没有在线状态或观测。")
            if client is None:
                with lab_client() as http:
                    return capture(binding, lab_run_id, investigation_id, name, http)
            before = states(client, binding)
            if name == "read_runtime_state":
                data, origin = before, "current_lab_state"
            elif name == "read_current_observations":
                origin = "current_lab_observations"
                for port in (8101, 8102):
                    raw = fetch(client, port, f"/control/observations/{binding.run_id}")
                    if not isinstance(raw, list) or len(raw) > 200:
                        raise fail("LIVE_SOURCE_INVALID", "观测列表不符合固定边界。")
                    for item in raw:
                        observation = Observation.model_validate(item)
                        if observation.phase != "failure":
                            continue
                        observed_now = datetime.now(timezone.utc)
                        if (
                            observation.request_id not in binding.request_ids
                            or observation.product_version != binding.product_version
                            or not binding.started_at <= observation.observed_at <= observed_now
                            or (observed_now - observation.observed_at).total_seconds() > 600
                        ):
                            raise fail(
                                "LIVE_OBSERVATION_SCOPE_INVALID",
                                "异常观测不属于本次绑定请求与时间窗口。",
                            )
                        data.append(observation.model_dump(mode="json", exclude_none=True))
                if len(data) > 40:
                    raise fail("LIVE_SOURCE_INVALID", "本次异常观测超过 40 条。")
                # 取证过程中发生重置不能合并两代实例的数据。
                states(client, binding)
                data.sort(key=lambda o: (o["observed_at"], o["service"], o["event"]))
            else:
                raise fail("TOOL_NOT_ALLOWED", "工具不在固定只读适配列表。")
    except ValidationError:
        raise fail("LIVE_SOURCE_INVALID", "实验状态或观测 Schema 无效。") from None
    now = datetime.now(timezone.utc)
    if now >= binding.expires_at:
        raise fail("LIVE_RUN_EXPIRED", "取证完成时登记已过期。")
    meta = {
        "source_type": origin,
        "lab_run_id": str(lab_run_id),
        "run_id": str(binding.run_id),
        "instance_id": str(binding.instance_id),
        "receiver_instance_id": str(binding.receiver_instance_id)
        if binding.receiver_instance_id
        else None,
        "captured_at": now.isoformat(),
        "registration_sha256": digest(binding.model_dump(mode="json")),
    }
    snapshot = {**meta, "sha256": digest({**meta, "data": data}), "content_sha256": digest(data)}
    evidence = []
    for ordinal, item in enumerate(data):
        identity = f"live:{lab_run_id}:{digest(item)}"
        evidence.append(
            {
                "evidence_id": identity,
                "kind": "log",
                "product_version": binding.product_version,
                "text": json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                "reference_url": f"/api/investigations/{investigation_id}/evidence/{identity}",
                "source": {**meta, "ordinal": ordinal, "snapshot_sha256": snapshot["sha256"]},
                "text_verified": True,
                "support_verified": False,
            }
        )
    value = {"evidence": evidence, "snapshot": snapshot}
    verify_snapshot(value)
    return value
