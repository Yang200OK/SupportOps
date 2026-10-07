"""真实 PostgreSQL、Redis、HTTP 路径；观测错误来自资源行为。"""

import json
import os
from contextlib import asynccontextmanager
from time import perf_counter
from uuid import UUID, uuid4

import httpx
import redis
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from redis.backoff import NoBackoff
from redis.retry import Retry
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.exc import TimeoutError as PoolTimeout

from supportops.lab.actions import register_actions
from supportops.lab.contracts import Observation, RelayConfig, Strict, Target
from supportops.lab.journal import Journal, control_token
from supportops.lab.receiver import Delivery


class TargetChange(Strict):
    target: Target
    invalidate: bool = True


class LabFailure(Exception):
    def __init__(self, status, code):
        self.status, self.code = status, code


class Runtime:
    def __init__(self, config: RelayConfig):
        self.config = config
        # 每次运行初始化 / reset 产生新身份，调查不能混用两代资源状态。
        self.instance_id = uuid4()
        self.engine = create_engine(
            os.environ["LAB_DATABASE_URL"],
            pool_size=config.db_pool_size,
            max_overflow=0,
            pool_timeout=config.db_pool_wait_ms / 1000,
            hide_parameters=True,
            connect_args={"connect_timeout": 3},
        )
        self.cache = redis.Redis.from_url(
            os.environ["LAB_REDIS_URL"],
            decode_responses=True,
            socket_timeout=2,
            socket_connect_timeout=2,
            retry=Retry(NoBackoff(), 0),
        )
        self.leases = []
        self.http = httpx.Client(base_url=os.environ["LAB_RECEIVER_URL"], trust_env=False)
        self.cache.ping()
        with self.engine.begin() as connection:
            if connection.scalar(text("SELECT current_database()")) != "relaydesk":
                raise RuntimeError("实验产品只能使用独立 relaydesk 数据库。")
            connection.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS target_config "
                    "(id integer PRIMARY KEY, target text NOT NULL, generation integer NOT NULL)"
                )
            )
            connection.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS deliveries "
                    "(request_id uuid PRIMARY KEY, status integer NOT NULL)"
                )
            )
            connection.execute(
                text("INSERT INTO target_config VALUES (1,'current',1) ON CONFLICT (id) DO NOTHING")
            )
            generation = connection.scalar(text("SELECT generation FROM target_config WHERE id=1"))
        self.cache.set("rd:active", generation, nx=True)

    def close(self):
        self.release()
        self.http.close()
        self.cache.close()
        self.engine.dispose()

    def reset(self):
        with self.engine.begin() as c:
            c.execute(text("UPDATE target_config SET target='current', generation=1 WHERE id=1"))
        keys = list(self.cache.scan_iter("rd:*"))
        if keys:
            self.cache.delete(*keys)
        self.cache.set("rd:active", 1)

    def release(self):
        for connection in self.leases:
            connection.close()
        self.leases.clear()

    def hold_pool(self):
        if self.leases:
            raise HTTPException(409, "连接已被占用，请先释放。")
        for _ in range(self.config.db_pool_size):
            self.leases.append(self.engine.connect())
        return {"checked_out": self.engine.pool.checkedout()}

    def change_target(self, change: TargetChange):
        with self.engine.begin() as c:
            generation = c.scalar(
                text(
                    "UPDATE target_config SET target=:target, "
                    "generation=generation+1 WHERE id=1 RETURNING generation"
                ),
                {"target": change.target},
            )
        if change.invalidate and self.config.product_version != "1.0":
            self.cache.set("rd:active", generation)
        return {"generation": generation}

    def clear_cache(self):
        with self.engine.connect() as c:
            generation = c.scalar(text("SELECT generation FROM target_config WHERE id=1"))
        keys = list(self.cache.scan_iter("rd:cache:*"))
        if keys:
            self.cache.delete(*keys)
        self.cache.set("rd:active", generation)

    def deliver(self, payload: Delivery, journal: Journal):
        started = perf_counter()
        context = payload.model_dump(exclude={"run_id"})

        def log(event, **details):
            journal.append(
                payload.run_id, Observation(**context, service="relaydesk", event=event, **details)
            )

        status, code = 200, None
        log("request_received", effective_config=self.config)
        try:
            with self.engine.begin() as c:
                log(
                    "db_acquired",
                    checked_out=self.engine.pool.checkedout(),
                    database="relaydesk",
                    elapsed_ms=round((perf_counter() - started) * 1000, 3),
                )
                row = c.execute(
                    text("SELECT target,generation FROM target_config WHERE id=1")
                ).one()
                generation = int(self.cache.get("rd:active"))
                key = (
                    "rd:cache:single"
                    if self.config.product_version == "1.0"
                    else f"rd:cache:{generation}"
                )
                cached = self.cache.get(key)
                if cached is None:
                    cached = json.dumps({"target": row.target, "generation": row.generation})
                    self.cache.set(key, cached, px=self.config.cache_ttl_ms)
                value = json.loads(cached)
                log(
                    "target_observed",
                    db_target=row.target,
                    db_generation=row.generation,
                    cache_target=value["target"],
                    cache_generation=value["generation"],
                )
                try:
                    if (
                        self.config.product_version == "2.0"
                        and value["generation"] != row.generation
                    ):
                        raise LabFailure(409, "RD_CACHE_STALE")
                    response = self.http.post(
                        "/deliver/" + value["target"],
                        json=payload.model_dump(mode="json"),
                        timeout=self.config.timeout_ms() / 1000,
                    )
                    if response.status_code == 410:
                        raise LabFailure(409, "RD_CACHE_STALE")
                    if response.status_code != 200:
                        raise LabFailure(502, "RD_DOWNSTREAM_ERROR")
                except httpx.TimeoutException:
                    status, code = 504, "RD_TIMEOUT"
                except LabFailure as exc:
                    status, code = exc.status, exc.code
                c.execute(
                    text("INSERT INTO deliveries VALUES (:id,:status)"),
                    {"id": payload.request_id, "status": status},
                )
        except PoolTimeout:
            status, code = 503, "RD_POOL_WAIT"
        except (SQLAlchemyError, redis.RedisError, httpx.NetworkError):
            status, code = 503, "RD_DEPENDENCY_UNAVAILABLE"
        log(
            "request_finished",
            status=status,
            error_code=code,
            checked_out=self.engine.pool.checkedout(),
            elapsed_ms=round((perf_counter() - started) * 1000, 3),
        )
        return JSONResponse(
            {"request_id": str(payload.request_id), "status": status, "error_code": code},
            status_code=status,
        )


def create_app():
    started = perf_counter()
    startup_instance_id = uuid4()
    raw = None
    try:
        raw = json.loads(os.environ["RELAYDESK_CONFIG"])
        config = RelayConfig.model_validate(raw)
    except (ValidationError, json.JSONDecodeError):
        elapsed = round((perf_counter() - started) * 1000, 3)
        report = {"status": 422, "error_code": "RD_CONFIG_INVALID", "elapsed_ms": elapsed}
        # 普通非法启动也要拒绝；追踪字段只决定是否附加可归档观测，不决定能否启动。
        if (
            isinstance(raw, dict)
            and raw.get("product_version") in ("1.0", "1.1", "2.0")
            and os.environ.get("LAB_REQUEST_ID")
            and os.environ.get("LAB_RUN_ID")
        ):
            event = Observation(
                request_id=UUID(os.environ["LAB_REQUEST_ID"]),
                phase="failure",
                service="boot",
                product_version=raw["product_version"],
                event="config_checked",
                status=422,
                error_code="RD_CONFIG_INVALID",
                elapsed_ms=elapsed,
                invalid_keys=[
                    k for k in ("delivery_timeout_ms", "downstream_timeout_ms") if k in raw
                ],
            )
            report = {
                "run_id": os.environ["LAB_RUN_ID"],
                **event.model_dump(mode="json", exclude_none=True),
            }
            # 新调查显式请求启动身份；旧三阶段导出继续保持原 Observation 格式。
            if os.environ.get("LAB_DIAGNOSTIC_CONTEXT") == "1":
                report["instance_id"] = str(startup_instance_id)
        print(json.dumps(report), flush=True)
        raise SystemExit(2) from None
    runtime = Runtime(config)
    journal = Journal()

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            app.state.runtime.close()
            app.state.action_store.close()

    app = FastAPI(title="RelayDesk local lab", lifespan=lifespan)
    app.state.runtime = runtime
    register_actions(app, journal, Runtime)

    @app.get("/health/live")
    def live():
        return {"service": "relaydesk", "config": app.state.runtime.config.effective()}

    @app.get("/diagnostics/state", dependencies=[Depends(control_token)])
    def diagnostic_state():
        runtime = app.state.runtime
        # 不申请新的数据库连接，连接池占满时仍能读取实际占用。
        return {
            "service": "relaydesk",
            "instance_id": str(runtime.instance_id),
            "config": runtime.config.effective(),
            "checked_out": runtime.engine.pool.checkedout(),
        }

    @app.post("/events")
    def events(payload: Delivery):
        if payload.product_version != app.state.runtime.config.product_version:
            raise HTTPException(409, "请求版本与实际实例不同。")
        return app.state.runtime.deliver(payload, journal)

    @app.post("/control/reset", dependencies=[Depends(control_token)])
    def reset(payload: RelayConfig):
        with app.state.control_lock:
            app.state.runtime.close()
            app.state.runtime = Runtime(payload)
            app.state.runtime.reset()
            return {"config": payload.effective()}

    @app.post("/control/pool/hold", dependencies=[Depends(control_token)])
    def hold():
        with app.state.control_lock:
            return app.state.runtime.hold_pool()

    @app.post("/control/pool/release", dependencies=[Depends(control_token)])
    def release():
        with app.state.control_lock:
            app.state.runtime.release()
            return {"released": True}

    @app.post("/control/target", dependencies=[Depends(control_token)])
    def target(payload: TargetChange):
        with app.state.control_lock:
            return app.state.runtime.change_target(payload)

    @app.post("/control/cache/clear", dependencies=[Depends(control_token)])
    def clear():
        with app.state.control_lock:
            app.state.runtime.clear_cache()
            return {"cleared": True}

    @app.get("/control/observations/{run_id}", dependencies=[Depends(control_token)])
    def observations(run_id: UUID):
        return journal.read(run_id)

    return app


app = create_app()
