"""独立本次实验 MCP，固定 GET 适配与只读数据库事务。"""

import os

import anyio
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, TextContent, Tool
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.orm import Session

from supportops.api.errors import ServiceError
from supportops.db.runtime import Database
from supportops.investigations.hypothesis_contracts import LiveScope, live_manifest
from supportops.investigations.live_tools import execute
from supportops.settings import Settings


async def main():
    scope = LiveScope.model_validate_json(os.environ["SUPPORTOPS_MCP_SCOPE"])
    database = Database(Settings(_env_file=None).database_url.get_secret_value())
    server = Server("supportops-hypothesis-readonly", version="1.0")

    @server.list_tools()
    async def list_tools():
        return [Tool.model_validate(t) for t in live_manifest()]

    @server.call_tool(validate_input=False)
    async def call_tool(name, arguments):
        try:
            with Session(database.engine) as session, session.begin():
                session.execute(text("SET TRANSACTION READ ONLY"))
                session.execute(text("SET LOCAL statement_timeout = '5000ms'"))
                database.check_ready(session)
                session.execute(
                    text("SELECT set_config('app.organization_id', :org, true)"),
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
