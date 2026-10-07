"""固定运行标识的真实 JSONL 观测；不接收故障标签。"""

import json
import os
import secrets
from threading import Lock
from uuid import UUID

from fastapi import Header, HTTPException

from supportops.lab.contracts import Observation


def control_token(value: str | None = Header(default=None, alias="X-Lab-Control")):
    expected = os.environ["LAB_CONTROL_TOKEN"]
    if value is None or not secrets.compare_digest(value, expected):
        raise HTTPException(403, "实验控制凭据不匹配。")


class Journal:
    def __init__(self):
        self.entries = {}
        self.lock = Lock()

    def append(self, run_id: UUID, observation: Observation):
        data = observation.model_dump(mode="json", exclude_none=True)
        with self.lock:
            if run_id not in self.entries and len(self.entries) >= 128:
                raise HTTPException(409, "实验进程运行记录超过 128，请显式重启实验服务。")
            records = self.entries.setdefault(run_id, [])
            if len(records) >= 200:
                raise HTTPException(409, "单运行观测超过 200。")
            records.append(data)
        print(json.dumps({"run_id": str(run_id), **data}, ensure_ascii=False), flush=True)

    def read(self, run_id: UUID):
        with self.lock:
            return list(self.entries.get(run_id, []))
