"""确定性主调度：独立取证先就绪，归并必须等待全部成功。"""

from copy import deepcopy

WORKFLOW = "bounded-coordination-plan.v1"
ROLES = {"documents": "document_agent", "runtime": "runtime_agent", "synthesis": "coordinator"}


def validate_dag(tasks):
    ids = [t["task_id"] for t in tasks]
    if not tasks or len(ids) != len(set(ids)):
        raise ValueError("任务身份必须唯一且非空。")
    dependencies = {t["task_id"]: t["depends_on"] for t in tasks}
    for identity, deps in dependencies.items():
        if len(deps) != len(set(deps)) or identity in deps or not set(deps) <= set(ids):
            raise ValueError("任务依赖无效。")
    visited, active = set(), set()

    def visit(identity):
        if identity in active:
            raise ValueError("任务依赖不能成环。")
        if identity in visited:
            return
        active.add(identity)
        for dependency in dependencies[identity]:
            visit(dependency)
        active.remove(identity)
        visited.add(identity)

    for identity in ids:
        visit(identity)


def ready_tasks(tasks, statuses):
    validate_dag(tasks)
    if not set(statuses) <= {t["task_id"] for t in tasks} or any(
        state not in {"pending", "running", "completed", "failed", "cancelled"}
        for state in statuses.values()
    ):
        raise ValueError("任务执行状态无效。")
    return [
        t["task_id"]
        for t in tasks
        if statuses.get(t["task_id"], "pending") == "pending"
        and all(statuses.get(d) == "completed" for d in t["depends_on"])
    ]


def split(total, count):
    return [total // count + (1 if n < total % count else 0) for n in range(count)]


def build_plan(ticket, mode, request):
    if mode not in {"online", "startup"}:
        raise ValueError("实验模式无效。")
    tool_shares = [*split(request.max_tool_calls, 2), 0]
    model_shares = split(request.max_model_calls, 3)
    char_shares = split(request.context_chars, 3)
    definitions = [
        (
            "documents",
            [],
            "核对同版本资料中的排查规则，历史资料不能证明本次根因。",
            ["search_knowledge"],
        ),
        (
            "runtime",
            [],
            "核对本次登记的现场证据，不把缺失信号解释为原因成立。",
            ["read_runtime_state", "read_current_observations"]
            if mode == "online"
            else ["read_startup_diagnostic"],
        ),
        ("synthesis", ["documents", "runtime"], "待两项取证成功后核对证据、缺口与冲突。", []),
    ]
    tasks = []
    for i, (identity, deps, objective, tools) in enumerate(definitions):
        package = {
            "task_id": identity,
            "role": ROLES[identity],
            "objective": objective,
            "ticket": {k: ticket[k] for k in ("title", "description", "product_version")},
            "tools": tools,
            "budget": {
                "tool_calls": tool_shares[i],
                "model_calls": model_shares[i],
                "context_chars": char_shares[i],
            },
            "output_requirement": "返回来源身份、原文依据和缺口；不可授予动作权限。",
        }
        tasks.append(
            {
                "task_id": identity,
                "role": ROLES[identity],
                "depends_on": deps,
                "package": deepcopy(package),
            }
        )
    validate_dag(tasks)
    return {
        "workflow_version": WORKFLOW,
        "tasks": tasks,
        "budget": {
            "tool_calls": request.max_tool_calls,
            "model_calls": request.max_model_calls,
            "context_chars": request.context_chars,
            "time_budget_ms": request.time_budget_ms,
        },
        "time_policy": "所有任务共用父截止时间；本轮未启动计时或执行。",
    }
