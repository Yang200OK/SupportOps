"""新工作流独立校验完整工具协议，不改变旧基线客户端。"""

import os
import sys
from contextlib import asynccontextmanager
from datetime import timedelta

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from supportops.investigations.hypothesis_contracts import LIVE_ARGUMENTS, live_manifest
from supportops.investigations.mcp_client import MCPFailure
from supportops.settings import ROOT


def check_manifest(items):
    expected = {t["name"]: t for t in live_manifest()}
    if len(items) != len(expected) or {t.name for t in items} != set(expected):
        raise MCPFailure("MCP_TOOL_MANIFEST_INVALID")
    for tool in items:
        known = expected[tool.name]
        if (
            tool.inputSchema != known["inputSchema"]
            or tool.description != known["description"]
            or tool.annotations is None
            or tool.annotations.model_dump(exclude_none=True) != known["annotations"]
        ):
            raise MCPFailure("MCP_TOOL_MANIFEST_INVALID")


class Tools:
    def __init__(self, client, initialization):
        self.client, self.initialization = client, initialization.model_dump(mode="json")

    async def call(self, name, arguments, seconds):
        if name not in LIVE_ARGUMENTS:
            raise MCPFailure("TOOL_NOT_ALLOWED")
        LIVE_ARGUMENTS[name].model_validate(arguments)
        result = await self.client.call_tool(
            name, arguments, read_timeout_seconds=timedelta(seconds=min(30, seconds))
        )
        if result.isError:
            code = (
                result.content[0].text
                if result.content and result.content[0].type == "text"
                else "MCP_TOOL_FAILED"
            )
            allowed = {
                "TOOL_NOT_ALLOWED",
                "TOOL_ARGUMENTS_INVALID",
                "AUTHENTICATION_REQUIRED",
                "INVESTIGATION_SCOPE_CHANGED",
                "INVESTIGATION_VERSION_MISMATCH",
                "INDEX_INTEGRITY_FAILED",
                "LIVE_RUN_EXPIRED",
                "LIVE_RUN_NOT_FOUND",
                "LIVE_REGISTRATION_INVALID",
                "LIVE_TOOL_MODE_INVALID",
                "LIVE_SOURCE_UNAVAILABLE",
                "LIVE_SOURCE_INVALID",
                "LIVE_INSTANCE_CHANGED",
                "LIVE_OBSERVATION_SCOPE_INVALID",
                "LIVE_SNAPSHOT_INVALID",
            }
            raise MCPFailure(code if code in allowed else "MCP_TOOL_FAILED")
        if not isinstance(result.structuredContent, dict) or not isinstance(
            result.structuredContent.get("evidence"), list
        ):
            raise MCPFailure("MCP_RESULT_INVALID")
        return result.structuredContent


@asynccontextmanager
async def connect(scope, database_url):
    environment = {
        k: os.environ[k]
        for k in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC", "PATH")
        if k in os.environ
    }
    environment.update(
        PYTHONUTF8="1",
        PYTHONPATH=str(ROOT / "src"),
        SUPPORTOPS_DATABASE_URL=database_url,
        SUPPORTOPS_MCP_SCOPE=scope.model_dump_json(),
    )
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "supportops.investigations.live_server"],
        env=environment,
        cwd=str(ROOT),
    )
    with open(os.devnull, "w") as errors:
        async with stdio_client(params, errlog=errors) as (read, write):
            async with ClientSession(
                read, write, read_timeout_seconds=timedelta(seconds=30)
            ) as client:
                initialized = await client.initialize()
                if (
                    initialized.serverInfo.name != "supportops-hypothesis-readonly"
                    or initialized.serverInfo.version != "1.0"
                ):
                    raise MCPFailure("MCP_SERVER_INVALID")
                discovered = await client.list_tools()
                check_manifest(discovered.tools)
                yield Tools(client, initialized)
