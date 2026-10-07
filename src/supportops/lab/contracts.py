"""公共观测采用严格字段，实验控制和答案不能混入证据包。"""

import hashlib
import json
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

Version = Literal["1.0", "1.1", "2.0"]
Phase = Literal["baseline", "failure", "retest"]
Target = Literal["current", "new"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, hide_input_in_errors=True)


class RelayConfig(Strict):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    product_version: Version
    delivery_timeout_ms: int | None = Field(default=None, ge=1, le=5000)
    downstream_timeout_ms: int | None = Field(default=None, ge=1, le=5000)
    db_pool_size: int = Field(ge=1, le=10)
    db_pool_wait_ms: int = Field(ge=1, le=5000)
    cache_ttl_ms: int = Field(default=60000, ge=1, le=60000)

    @model_validator(mode="before")
    @classmethod
    def explicit_version_defaults(cls, value):
        if not isinstance(value, dict):
            return value
        data = dict(value)
        version = data.get("product_version")
        if version not in ("1.0", "1.1", "2.0"):
            raise ValueError("必须指定受支持的产品版本。")
        key = "downstream_timeout_ms" if version == "2.0" else "delivery_timeout_ms"
        forbidden = "delivery_timeout_ms" if version == "2.0" else "downstream_timeout_ms"
        if forbidden in data:
            raise ValueError("超时参数名称与产品版本不匹配。")
        data.setdefault(key, 2000 if version == "1.0" else 3000)
        if data[key] is None:
            raise ValueError("超时配置不能为 null。")
        data.setdefault("db_pool_size", 5 if version == "1.0" else 10)
        data.setdefault("db_pool_wait_ms", 1000 if version == "1.0" else 1500)
        return data

    def effective(self):
        return self.model_dump(exclude_none=True)

    def timeout_ms(self):
        return self.effective()[
            "downstream_timeout_ms" if self.product_version == "2.0" else "delivery_timeout_ms"
        ]


class Observation(Strict):
    request_id: UUID
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    phase: Phase
    service: Literal["relaydesk", "receiver", "boot"]
    product_version: Version
    event: Literal[
        "config_checked",
        "request_received",
        "db_acquired",
        "target_observed",
        "downstream_received",
        "downstream_finished",
        "request_finished",
    ]
    status: int | None = Field(default=None, ge=100, le=599)
    error_code: (
        Literal[
            "RD_CONFIG_INVALID",
            "RD_POOL_WAIT",
            "RD_CACHE_STALE",
            "RD_TIMEOUT",
            "RD_DEPENDENCY_UNAVAILABLE",
            "RD_DOWNSTREAM_ERROR",
        ]
        | None
    ) = None
    elapsed_ms: float | None = Field(default=None, ge=0)
    effective_config: RelayConfig | None = None
    invalid_keys: list[Literal["delivery_timeout_ms", "downstream_timeout_ms"]] = Field(
        default_factory=list
    )
    database: Literal["relaydesk"] | None = None
    checked_out: int | None = Field(default=None, ge=0, le=10)
    db_target: Target | None = None
    cache_target: Target | None = None
    db_generation: int | None = Field(default=None, ge=1)
    cache_generation: int | None = Field(default=None, ge=1)
    target: Target | None = None
    delay_ms: int | None = Field(default=None, ge=0, le=5000)

    @model_validator(mode="after")
    def meaningful_and_versioned(self):
        if self.observed_at.tzinfo is None:
            raise ValueError("观测时间必须包含时区。")
        if self.effective_config and self.effective_config.product_version != self.product_version:
            raise ValueError("有效配置与观测版本不同。")
        if self.event in ("request_finished", "config_checked") and (
            self.status is None or self.elapsed_ms is None
        ):
            raise ValueError("结果事件必须包含状态与实测耗时。")
        return self


class LabBundle(Strict):
    schema_version: Literal["supportops.lab-observations.v1"] = "supportops.lab-observations.v1"
    run_id: UUID
    product_version: Version
    observations: list[Observation] = Field(min_length=3, max_length=200)

    @model_validator(mode="after")
    def complete_phases(self):
        if any(o.product_version != self.product_version for o in self.observations):
            raise ValueError("一个观测包只能包含同一产品版本。")
        for phase in ("baseline", "failure", "retest"):
            finished = [
                o
                for o in self.observations
                if o.phase == phase and o.event in ("request_finished", "config_checked")
            ]
            if len(finished) != 1:
                raise ValueError("每个阶段必须有唯一结果，包含失败结果。")
        return self

    def canonical(self):
        return json.dumps(
            self.model_dump(mode="json", exclude_none=True),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def digest(self):
        return hashlib.sha256(self.canonical().encode("utf-8")).hexdigest()

    def evidence_ids(self):
        return [f"lab:{self.run_id}:{index}" for index in range(len(self.observations))]
