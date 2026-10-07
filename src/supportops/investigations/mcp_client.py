"""真实 MCP 握手 / 发现 / 调用；不提供直接函数调用替代路径。"""

import os
import sys
from contextlib import asynccontextmanager
from datetime import timedelta

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from supportops.investigations.contracts import ARGUMENTS, tool_manifest
from supportops.settings import ROOT


class MCPFailure(Exception):
    def __init__(self, code):
        self.code = code


def check_manifest(items):
    expected = {t["name"]: t for t in tool_manifest()}
    if len(items) != len(expected) or {t.name for t in items} != set(expected):
        raise MCPFailure("MCP_TOOL_MANIFEST_INVALID")
    for item in items:
        known = expected[item.name]
        if (
            item.inputSchema != known["inputSchema"]
            or item.description != known["description"]
            or item.annotations is None
            or item.annotations.model_dump(exclude_none=True) != known["annotations"]
        ):
            raise MCPFailure("MCP_TOOL_MANIFEST_INVALID")


class Tools:
    def __init__(self, client, initialization):
        self.client = client
        self.initialization = initialization.model_dump(mode="json")

    async def call(self, name, arguments, seconds):
        if name not in ARGUMENTS:
            raise MCPFailure("TOOL_NOT_ALLOWED")
        ARGUMENTS[name].model_validate(arguments)
        result = await self.client.call_tool(
            name, arguments, read_timeout_seconds=timedelta(seconds=min(15, seconds))
        )
        if result.isError:
            code = (
                result.content[0].text
                if result.content and result.content[0].type == "text"
                else "MCP_TOOL_FAILED"
            )
            # 公共轨迹只记录明确代码，不暴露原生 SQL / 连接错误正文。
            allowed = {
                "TOOL_NOT_ALLOWED",
                "TOOL_ARGUMENTS_INVALID",
                "AUTHENTICATION_REQUIRED",
                "INVESTIGATION_SCOPE_CHANGED",
                "INVESTIGATION_VERSION_MISMATCH",
                "OBSERVATION_INTEGRITY_FAILED",
                "INDEX_INTEGRITY_FAILED",
            }
            raise MCPFailure(code if code in allowed else "MCP_TOOL_FAILED")
        if not isinstance(result.structuredContent, dict) or not isinstance(
            result.structuredContent.get("evidence"), list
        ):
            raise MCPFailure("MCP_RESULT_INVALID")
        return result.structuredContent


@asynccontextmanager
async def connect(scope, database_url):
    # 不继承整份环境：模型密钥、实验控制令牌和管理员 URL 不进入子进程。
    environment = {
        key: os.environ[key]
        for key in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC", "PATH")
        if key in os.environ
    }
    environment.update(
        PYTHONUTF8="1",
        PYTHONPATH=str(ROOT / "src"),
        SUPPORTOPS_DATABASE_URL=database_url,
        SUPPORTOPS_MCP_SCOPE=scope.model_dump_json(),
    )
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "supportops.investigations.mcp_server"],
        env=environment,
        cwd=str(ROOT),
    )
    with open(os.devnull, "w") as errors:
        async with stdio_client(params, errlog=errors) as (read, write):
            async with ClientSession(
                read, write, read_timeout_seconds=timedelta(seconds=15)
            ) as client:
                initialization = await client.initialize()
                if (
                    initialization.serverInfo.name != "supportops-readonly"
                    or initialization.serverInfo.version != "1.0"
                ):
                    raise MCPFailure("MCP_SERVER_INVALID")
                result = await client.list_tools()
                check_manifest(result.tools)
                yield Tools(client, initialization)
