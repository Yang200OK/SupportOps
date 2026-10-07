"""角色私有 MCP：服务器复查已预留调用、会话、租约与固定来源。"""

import os

import anyio
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, TextContent, Tool
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.orm import Session

from supportops.actions.service import authenticate
from supportops.api.errors import ServiceError
from supportops.auth.service import Principal
from supportops.chunks.chunking import digest
from supportops.coordination.envelope import normalize_live
from supportops.coordination.execution_contracts import RoleScope
from supportops.coordination.execution_service import fresh, get
from supportops.coordination.execution_service import scope as expected_scope
from supportops.db.runtime import Database
from supportops.investigations.hypothesis_contracts import LIVE_ARGUMENTS, live_manifest
from supportops.investigations.live_tools import execute as live_execute
from supportops.retrieval import hybrid
from supportops.retrieval.contracts import HybridSearch
from supportops.settings import Settings


def manifest(names):
    items = [t for t in live_manifest() if t["name"] in names]
    for item in items:
        if item["name"] == "search_knowledge":
            item["description"] = (
                "角色私有同版本 BM25，每次最多一段文档 / 案例；资料不能证明本次根因。"
            )
    return items


def execute(session, scope, name, arguments):
    principal = Principal(scope.user_id, "mcp", scope.organization_id, "mcp", scope.session_id)
    authenticate(session, principal)
    row = get(session, principal, scope.execution_id)
    # 工具子进程不加载回答模型设置或密钥，模型绑定由父执行器检查。
    board, package = fresh(session, principal, row, scope.task_id, scope.lease, check_model=False)
    if scope != expected_scope(row, board, scope.task_id, scope.lease, scope.operation_key):
        raise ServiceError(403, "COORDINATION_ROLE_DENIED", "执行范围与服务器绑定不一致。")
    if name not in package["tools"]:
        raise ServiceError(403, "COORDINATION_ROLE_DENIED", "工具不属于当前角色。")
    LIVE_ARGUMENTS[name].model_validate(arguments)
    operation = next(
        (
            o
            for o in row.state["operations"]
            if o["task"] == scope.task_id and o["key"] == scope.operation_key
        ),
        None,
    )
    if (
        operation is None
        or operation["status"] != "started"
        or operation["kind"] != "tool"
        or operation["proposal_sha256"] != digest({"tool": name, "arguments": arguments})
    ):
        raise ServiceError(403, "COORDINATION_ROLE_DENIED", "工具调用没有匹配的预留记录。")
    if name == "search_knowledge":
        # 沿用原工具的范围核对，采用本协议明确的一段窗口，不改旧三段工具。
        live_execute(session, scope, "get_ticket", {})
        result = hybrid.search(
            session,
            principal,
            scope.index_id,
            HybridSearch(
                query=arguments["query"],
                product_version=scope.product_version,
                source_kinds=["document", "case"],
                top_k=1,
                candidate_limit=10,
                mode="bm25",
            ),
        )
        return {"evidence": result["items"], "mode": "bm25", "corpus_sha256": scope.corpus_sha256}
    value = live_execute(session, scope, name, arguments)
    for item in value["evidence"]:
        item["reference_url"] = (
            f"/api/coordination-executions/{scope.execution_id}/evidence/{item['evidence_id']}"
        )
    return normalize_live(value)


async def main():
    scope = RoleScope.model_validate_json(os.environ["SUPPORTOPS_MCP_SCOPE"])
    database = Database(Settings(_env_file=None).database_url.get_secret_value())
    principal = Principal(scope.user_id, "mcp", scope.organization_id, "mcp", scope.session_id)
    # 发现协议也来自数据库固定任务，而不是子进程参数自报的角色权限。
    with Session(database.engine) as session, session.begin():
        database.check_ready(session)
        session.execute(
            text("SELECT set_config('app.organization_id',:org,true)"),
            {"org": str(scope.organization_id)},
        )
        row = get(session, principal, scope.execution_id)
        _, package = fresh(session, principal, row, scope.task_id, scope.lease, check_model=False)
        items = manifest(package["tools"])
    server = Server("supportops-coordination-role", version="1.0")
    spent = False

    @server.list_tools()
    async def list_tools():
        return [Tool.model_validate(t) for t in items]

    @server.call_tool(validate_input=False)
    async def call_tool(name, arguments):
        nonlocal spent
        try:
            if name not in package["tools"] or spent:
                raise ServiceError(403, "COORDINATION_ROLE_DENIED", "角色或预留调用已无权限。")
            # 每个子进程仅对应一次预留调用；同一回执窗口内也不能多次取证。
            spent = True
            with Session(database.engine) as session, session.begin():
                session.execute(text("SET TRANSACTION READ ONLY"))
                session.execute(text("SET LOCAL statement_timeout = '5000ms'"))
                database.check_ready(session)
                session.execute(
                    text("SELECT set_config('app.organization_id',:org,true)"),
                    {"org": str(scope.organization_id)},
                )
                value = execute(session, scope, name, arguments)
            return CallToolResult(content=[], structuredContent=value)
        except (ServiceError, ValidationError) as error:
            code = error.code if isinstance(error, ServiceError) else "TOOL_ARGUMENTS_INVALID"
            return CallToolResult(isError=True, content=[TextContent(type="text", text=code)])

    try:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())
    finally:
        database.engine.dispose()


if __name__ == "__main__":
    anyio.run(main)
