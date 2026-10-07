"""预算在调用之前检查，迟到的输出不能越过停止门槛。"""

import json
from time import perf_counter


class Gate:
    def __init__(self, max_tools, max_models, time_ms, clock=perf_counter):
        self.max_tools, self.max_models, self.time_ms = max_tools, max_models, time_ms
        self.clock, self.started = clock, clock()
        self.tools, self.models, self.chars = [], 0, 0

    def elapsed(self):
        return max(0, int((self.clock() - self.started) * 1000))

    def check_time(self):
        if self.elapsed() >= self.time_ms:
            raise ValueError("time_budget")

    def seconds(self):
        self.check_time()
        return max(0.001, (self.time_ms - self.elapsed()) / 1000)

    def model(self):
        self.check_time()
        if self.models >= self.max_models:
            raise ValueError("model_budget")
        self.models += 1

    def tool(self, name, arguments):
        self.check_time()
        if len(self.tools) >= self.max_tools:
            raise ValueError("tool_budget")
        if name in self.tools:
            raise ValueError("repeated_tool")
        self.tools.append(name)

    def result(self, value):
        size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
        if self.chars + size > 12000:
            raise ValueError("context_budget")
        self.chars += size
