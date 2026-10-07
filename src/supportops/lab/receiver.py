"""真正等待后响应的下游接收器；开关只在实验控制接口。"""

import asyncio
from time import perf_counter
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from pydantic import Field

from supportops.lab.contracts import Observation, Phase, Strict, Target, Version
from supportops.lab.journal import Journal, control_token


class ReceiverPolicy(Strict):
    active_target: Target = "current"
    delay_ms: int = Field(default=0, ge=0, le=5000, strict=True)


class Delivery(Strict):
    run_id: UUID
    request_id: UUID
    product_version: Version
    phase: Phase


app = FastAPI(title="RelayDesk lab receiver")
journal = Journal()
policy = ReceiverPolicy()
instance_id = uuid4()


@app.get("/health/live")
def live():
    return {"service": "receiver", "status": "alive"}


@app.get("/diagnostics/state", dependencies=[Depends(control_token)])
def diagnostic_state():
    return {"service": "receiver", "instance_id": str(instance_id), **policy.model_dump()}


@app.post("/control/settings", dependencies=[Depends(control_token)])
def settings(payload: ReceiverPolicy):
    global policy, instance_id
    policy = payload
    # 生效策略变化视为新一代运行，旧调查必须重新登记。
    instance_id = uuid4()
    return payload


@app.get("/control/observations/{run_id}", dependencies=[Depends(control_token)])
def observations(run_id: UUID):
    return journal.read(run_id)


@app.post("/deliver/{target}")
async def deliver(target: Target, payload: Delivery):
    started = perf_counter()
    current = policy
    context = payload.model_dump(exclude={"run_id"})
    journal.append(
        payload.run_id,
        Observation(
            **context,
            service="receiver",
            event="downstream_received",
            target=target,
            delay_ms=current.delay_ms,
        ),
    )
    await asyncio.sleep(current.delay_ms / 1000)
    status = 200 if target == current.active_target else 410
    journal.append(
        payload.run_id,
        Observation(
            **context,
            service="receiver",
            event="downstream_finished",
            target=target,
            delay_ms=current.delay_ms,
            status=status,
            elapsed_ms=round((perf_counter() - started) * 1000, 3),
        ),
    )
    return JSONResponse({"accepted": status == 200}, status_code=status)
