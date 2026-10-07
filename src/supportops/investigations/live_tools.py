"""每次只读 MCP 调用重新验证会话、工单、快照和实验登记。"""

from supportops.api.errors import ServiceError
from supportops.auth.service import Principal
from supportops.investigations import tools
from supportops.investigations.hypothesis_contracts import LIVE_ARGUMENTS
from supportops.investigations.live_service import get_registration
from supportops.investigations.live_sources import capture


def execute(session, scope, name, arguments):
    if name not in LIVE_ARGUMENTS:
        raise ServiceError(403, "TOOL_NOT_ALLOWED", "工具不在本轮白名单。")
    LIVE_ARGUMENTS[name].model_validate(arguments)
    checked = tools.execute(session, scope, "get_ticket", {})
    principal = Principal(scope.user_id, "mcp", scope.organization_id, "mcp", scope.session_id)
    record, binding = get_registration(session, principal, scope.lab_run_id)
    if (
        record.sha256 != scope.registration_sha256
        or binding.product_version != scope.product_version
    ):
        raise ServiceError(409, "INVESTIGATION_SCOPE_CHANGED", "固定实验登记已变化。")
    if name == "get_ticket":
        # 给模型的陈述去掉用户 / 组织身份，范围仍由服务器独立验证。
        checked["ticket"] = {
            k: v
            for k, v in checked["ticket"].items()
            if k not in ("organization_id", "requester_id")
        }
        return checked
    if name == "search_knowledge":
        return tools.execute(session, scope, name, arguments)
    return capture(binding, scope.lab_run_id, scope.investigation_id, name)
