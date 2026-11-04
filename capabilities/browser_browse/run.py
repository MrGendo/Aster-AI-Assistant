# 浏览器能力 worker（browser-use + chromium）：动态页面读取与简单交互
# 定位：web_fetch 的升级版——JS 渲染页面、需要点击/滚动后才能看到的内容
# 配置路线（实测收敛）：
#   1. LLM 用 browser_use 自带 ChatAnthropic（原生 langchain 类缺 provider 属性会被拒）
#   2. GLM 走 ANTHROPIC_BASE_URL 环境变量（与 claude CLI 同源）
#   3. NO_PROXY 必须旁路 localhost——否则 SOCKS 代理拦截本地 CDP 调试端口
# 风险说明：浏览器可交互（点击/输入），故为 L1：默认需确认，可在设置中逐能力开自动
import asyncio
import json
import os
import sys
from pathlib import Path


def main() -> int:
    try:
        p = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        print("输入不是合法 JSON", file=sys.stderr)
        return 4
    url = (p.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        print(json.dumps({"error": "需要 http(s) 链接"}, ensure_ascii=False))
        return 4
    instruction = p.get("instruction") or "读取页面主要内容并总结要点"
    max_steps = min(int(p.get("max_steps", 8)), 15)  # 步数上限防失控

    env = json.loads((Path.home() / ".claude/settings.json").read_text()).get("env", {})
    os.environ["ANTHROPIC_BASE_URL"] = env.get("ANTHROPIC_BASE_URL",
                                               "https://open.bigmodel.cn/api/anthropic")
    os.environ["ANTHROPIC_API_KEY"] = env["ANTHROPIC_AUTH_TOKEN"]
    os.environ["NO_PROXY"] = os.environ["no_proxy"] = "localhost,127.0.0.1,open.bigmodel.cn"

    async def run():
        from browser_use import Agent, ChatAnthropic
        llm = ChatAnthropic(model="glm-4.7", max_tokens=1024)
        # 任务指令固定附加只读约束：本能力定位是"读"，防指令注入把浏览器带去干别的
        # URL 必须独立成行——与中文同行会被 URL 提取贪婪吞入 netloc（实测教训）
        task = (f"{url}\n"
                f"任务：{instruction}\n"
                "约束：除非任务明确要求，不要点击提交类按钮、不要输入任何表单、"
                "不要登录任何账号；完成即返回结果文本。")
        agent = Agent(task=task, llm=llm)
        r = await agent.run(max_steps=max_steps)
        return r.final_result() or ""

    try:
        result = asyncio.run(run())
    except Exception as e:
        print(json.dumps({"error": f"浏览器任务失败：{e}"}, ensure_ascii=False))
        return 2
    if not result.strip():
        print(json.dumps({"error": "浏览器任务完成但无结果文本"}, ensure_ascii=False))
        return 2
    print(json.dumps({"url": url, "result": result[:4000]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
