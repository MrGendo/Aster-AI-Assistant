# PoC-6：语雀官方 MCP Server 连通性验证
# 用法：YUQUE_PERSONAL_TOKEN=xxx poc/.venv/bin/python probe.py
# Token 仅经环境变量传入子进程，不落任何文件（用后即弃）
import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

TOKEN = os.environ.get("YUQUE_PERSONAL_TOKEN", "")
if not TOKEN:
    sys.exit("缺少 YUQUE_PERSONAL_TOKEN 环境变量——请从 语雀→设置→Token 创建后传入")


async def main():
    # 官方推荐拉起方式：npx -y yuque-mcp@latest（自动跟随最新版）
    params = StdioServerParameters(
        command="npx",
        args=["-y", "yuque-mcp@latest"],
        env={"YUQUE_PERSONAL_TOKEN": TOKEN},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # 1) 工具清单（与 README 声称的 19 个对比差异）
            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            print(f"[1] 工具总数: {len(names)}")
            for n in names:
                print(f"    - {n}")

            # 2) 真实搜索（改成一个通用词，确保有结果）
            res = await session.call_tool("yuque_search", {"keyword": "笔记"})
            text = (res.content[0].text if res.content else "")[:300]
            print(f"[2] yuque_search 返回片段: {text}")
            if res.isError:
                print("    搜索返回错误！")
            else:
                print("    搜索连通 OK")


if __name__ == "__main__":
    asyncio.run(main())
