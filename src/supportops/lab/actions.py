"""固定实验端的持久化幂等日志；未知效果不自动重做。"""

import json
import os
from datetime import datetime, timezone
from threading import RLock
from uuid import UUID

from fastapi import Depends, HTTPException
from fastapi.responses import JSONResponse

from supportops.actions.contracts import LabCommand
from supportops.chunks.chunking import digest
from supportops.lab.contracts import RelayConfig
from supportops.lab.journal import control_token
from supportops.lab.receiver import Delivery


class ActionJournal:
    def __init__(self, store):
        self.store = store

    def read(self, identity):
        raw = self.store.get("supportops:action:" + str(identity))
        return json.loads(raw) if raw is not None else None

    def execute(self, command, effect):
        key = "supportops:action:" + str(command.action_id)
        sha = digest(command.model_dump(mode="json"))
        started = {"command_sha256": sha, "status": "started"}
        if not self.store.set(key, json.dumps(started), nx=True):
            old = self.read(command.action_id)
            if old["command_sha256"] != sha:
                raise HTTPException(409, "ACTION_KEY_CONFLICT")
            if old["status"] != "completed":
                raise HTTPException(409, "ACTION_UNCERTAIN")
            return old["receipt"]
        # 开始标记先写入 AOF；执行与完成记录之间崩溃只能报告未知。
        result = effect()
        receipt = {
            "action_id": str(command.action_id),
            "command_sha256": sha,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "result": result,
        }
        receipt["sha256"] = digest(receipt)
        self.store.set(key, json.dumps({**started, "status": "completed", "receipt": receipt}))
        return receipt


def register_actions(app, observations, runtime_factory):
    # 原控制接口与新动作使用同一把锁，防止 reset 在检查和执行间换代。
    lock = RLock()
    app.state.control_lock = lock
    # Redis 客户端独立于 Runtime.close，产品运行时重建不清除动作回执。
    import redis
    from redis.backoff import NoBackoff
    from redis.retry import Retry

    store = redis.Redis.from_url(
        os.environ["LAB_REDIS_URL"],
        decode_responses=True,
        socket_timeout=2,
        socket_connect_timeout=2,
        retry=Retry(NoBackoff(), 0),
    )
    app.state.action_store = store
    journal = ActionJournal(store)

    @app.get("/control/actions/{identity}", dependencies=[Depends(control_token)])
    def receipt(identity: UUID):
        value = journal.read(identity)
        if value is None:
            raise HTTPException(404, "ACTION_RECEIPT_NOT_FOUND")
        return value

    @app.post("/control/actions", dependencies=[Depends(control_token)])
    def execute(command: LabCommand):
        with lock:

            def effect():
                runtime = app.state.runtime
                if (
                    runtime.instance_id != command.instance_id
                    or runtime.config.product_version != command.product_version
                ):
                    raise HTTPException(409, "LIVE_INSTANCE_CHANGED")
                remote = runtime.http.get(
                    "/diagnostics/state",
                    headers={"X-Lab-Control": os.environ["LAB_CONTROL_TOKEN"]},
                    timeout=5,
                )
                remote.raise_for_status()
                receiver = remote.json()
                if receiver["instance_id"] != str(command.receiver_instance_id):
                    raise HTTPException(409, "LIVE_INSTANCE_CHANGED")
                before = {
                    "instance_id": str(runtime.instance_id),
                    "config": runtime.config.effective(),
                    "checked_out": runtime.engine.pool.checkedout(),
                }
                if digest(before) != command.state_sha256:
                    raise HTTPException(409, "ACTION_STATE_CHANGED")
                outcome = {}
                if command.operation == "release_pool":
                    runtime.release()
                    outcome = {"released": True}
                elif command.operation == "clear_cache":
                    runtime.clear_cache()
                    outcome = {"cleared": True}
                elif command.operation == "set_timeout":
                    data = runtime.config.effective()
                    name = (
                        "downstream_timeout_ms"
                        if command.product_version == "2.0"
                        else "delivery_timeout_ms"
                    )
                    data[name] = command.timeout_ms
                    runtime.config = RelayConfig.model_validate(data)
                    outcome = {"changed_key": name, "timeout_ms": command.timeout_ms}
                elif command.operation == "restart_product":
                    config = runtime.config
                    runtime.close()
                    app.state.runtime = runtime_factory(config)
                    outcome = {"restarted": True}
                else:
                    response = runtime.deliver(
                        Delivery(
                            run_id=command.run_id,
                            request_id=command.request_id,
                            product_version=command.product_version,
                            phase="retest",
                        ),
                        observations,
                    )
                    outcome = {
                        "response": json.loads(response.body),
                        "observations": observations.read(command.run_id),
                    }
                runtime = app.state.runtime
                return {
                    "operation": command.operation,
                    "before": before,
                    "after": {
                        "instance_id": str(runtime.instance_id),
                        "config": runtime.config.effective(),
                        "checked_out": runtime.engine.pool.checkedout(),
                    },
                    "receiver": receiver,
                    **outcome,
                }

            return JSONResponse(journal.execute(command, effect))
