"""图之外的业务预算；相同参数只有新增证据后才允许再读。"""

import json
from time import perf_counter

from supportops.investigations.guard import Gate


class LiveGate(Gate):
    def __init__(self, max_tools, max_models, time_ms, clock=perf_counter):
        super().__init__(max_tools, max_models, time_ms, clock)
        self.seen = {}

    def model(self, reserve=0):
        self.check_time()
        if self.models + reserve >= self.max_models:
            raise ValueError("model_budget")
        self.models += 1

    def tool(self, name, arguments, evidence_digest):
        self.check_time()
        if len(self.tools) >= self.max_tools:
            raise ValueError("tool_budget")
        normalized = {
            k: " ".join(v.split()).casefold() if isinstance(v, str) else v
            for k, v in arguments.items()
        }
        key = name + json.dumps(normalized, ensure_ascii=False, sort_keys=True)
        if self.seen.get(key) == evidence_digest:
            raise ValueError("repeated_tool")
        self.seen[key] = evidence_digest
        self.tools.append(name)

    def result(self, value):
        size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
        if self.chars + size > 16000:
            raise ValueError("context_budget")
        self.chars += size
