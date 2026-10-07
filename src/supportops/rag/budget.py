"""调用与时间预算由代码控制，模型不能自行增加额度。"""

from time import perf_counter


class BudgetStop(Exception):
    pass


class Budget:
    def __init__(self, max_calls, time_ms, clock=perf_counter):
        self.max_calls = max_calls
        self.time_ms = time_ms
        self.clock = clock
        self.started = clock()
        self.records = []

    def elapsed_ms(self):
        return (self.clock() - self.started) * 1000

    def before(self, calls, *, minimum_seconds=0):
        usage = self.usage()
        if self.elapsed_ms() >= self.time_ms or self.remaining_seconds() < minimum_seconds:
            raise BudgetStop("time_budget")
        if usage["known_model_calls"] + usage["unknown_usage_calls"] + calls > self.max_calls:
            raise BudgetStop("model_budget")

    def remaining_seconds(self):
        return max(0, (self.time_ms - self.elapsed_ms()) / 1000)

    def timeout(self, calls=1):
        self.before(calls)
        seconds = min(60, self.remaining_seconds() / calls)
        if seconds < 1:
            raise BudgetStop("time_budget")
        return seconds

    def record(self, stage, usage, *, calls=None, unknown=0):
        if calls is None:
            calls = usage.get("model_calls", int(usage.get("model_called", True))) if usage else 0
        self.records.append(
            {
                "stage": stage,
                "usage": usage,
                "known_model_calls": calls,
                "unknown_usage_calls": unknown,
            }
        )

    def usage(self):
        return {
            "stages": self.records,
            "input_tokens": sum(
                (r["usage"] or {}).get("input_tokens", 0) or 0 for r in self.records
            ),
            "output_tokens": sum(
                (r["usage"] or {}).get("output_tokens", 0) or 0 for r in self.records
            ),
            "known_model_calls": sum(r["known_model_calls"] for r in self.records),
            "unknown_usage_calls": sum(r["unknown_usage_calls"] for r in self.records),
            "cost_cny": None,
        }
