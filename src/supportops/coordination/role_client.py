"""独立 stdio 连接，仅发现和允许当前角色的固定协议。"""

import os
import sys
from contextlib import asynccontextmanager
from datetime import timedelta

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from supportops.coordination.role_server import manifest
from supportops.investigations.live_client import Tools
from supportops.investigations.mcp_client import MCPFailure
from supportops.settings import ROOT


class RoleTools(Tools):
    def __init__(self, client, initialization, names):
        super().__init__(client, initialization)
        self.names = names

    async def call(self, name, arguments, seconds):
        if name not in self.names:
            raise MCPFailure("COORDINATION_ROLE_DENIED")
        return await super().call(name, arguments, seconds)


@asynccontextmanager
async def connect(scope, database_url, names):
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
        args=["-m", "supportops.coordination.role_server"],
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
                    initialized.serverInfo.name != "supportops-coordination-role"
                    or initialized.serverInfo.version != "1.0"
                ):
                    raise MCPFailure("MCP_SERVER_INVALID")
                discovered = await client.list_tools()
                expected = {t["name"]: t for t in manifest(names)}
                if len(discovered.tools) != len(expected) or {
                    t.name for t in discovered.tools
                } != set(expected):
                    raise MCPFailure("MCP_TOOL_MANIFEST_INVALID")
                for tool in discovered.tools:
                    item = expected[tool.name]
                    if (
                        tool.inputSchema != item["inputSchema"]
                        or tool.description != item["description"]
                        or tool.annotations is None
                        or tool.annotations.model_dump(exclude_none=True) != item["annotations"]
                    ):
                        raise MCPFailure("MCP_TOOL_MANIFEST_INVALID")
                yield RoleTools(client, initialized, names)
