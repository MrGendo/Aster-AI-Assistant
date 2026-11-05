# FastAPI 应用：REST + SSE（任务进度轮询推送）
from ..db import connect, migrate
from ..gateway import core as gateway
from ..providers import router as provider
from ..runtime import registry
from . import caps, chat, discovery, evolution, memories, schedules, system, tasks, todos


def create_app() -> "FastAPI":
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware

    app = FastAPI(title="Aster Platform", version="0.1.0")
    # 本地 Web 前端跨源
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"], allow_headers=["*"],
    )

    @app.on_event("startup")
    def _startup() -> None:
        conn = connect()
        migrate(conn)
        registry.ensure_builtin(conn)
        gateway.expire_stale(conn)
        provider.bootstrap_from_claude_cli(conn)  # 首次启动复用 claude CLI 的 GLM 配置
        # Stage 4：固定回放集种子（须在 migrate 之后）
        with conn:
            for name, goal, ctrl in [
                ("仓库分析", "分析 https://github.com/asg017/sqlite-vec 的基本情况", 0),
                ("热点日报", "把这几条热点整理成日报：A 发布新模型；B 发布新模型引起讨论；C 通过新法案", 0),
                ("对照-文本统计", "统计这段文本的字数：受控进化对照任务", 1),
            ]:
                conn.execute("INSERT OR IGNORE INTO replay_set(name, task_goal, is_control) "
                             "VALUES(?,?,?)", (name, goal, ctrl))
        conn.close()
        from ..watch import loop as watch_loop
        watch_loop.start()  # Stage 2：watchlist 后台巡检
        from .. import scheduler as job_sched
        job_sched.start()  # 定时推送/闹钟：启动时从 schedules 表全量重注册

    app.include_router(tasks.router, prefix="/api")
    app.include_router(caps.router, prefix="/api")
    app.include_router(memories.router, prefix="/api")
    app.include_router(system.router, prefix="/api")
    app.include_router(discovery.router, prefix="/api")
    app.include_router(evolution.router, prefix="/api")
    app.include_router(chat.router, prefix="/api")
    app.include_router(todos.router, prefix="/api")
    app.include_router(schedules.router, prefix="/api")

    # 前端产物：构建后由服务直接托管（API 路由先注册，静态挂载兜底其余路径）
    from starlette.staticfiles import StaticFiles
    from ..config import ROOT
    dist = ROOT / "web" / "dist"

    # 缓存策略（vite 部署标准）：index.html 必须每次回源校验，否则浏览器会
    # 启发式缓存旧页面、引用已被新构建删除的旧哈希资源——DAG 箭头"改了却
    # 看不到"两次返工的直接原因；哈希资源内容变名字变，可永久缓存
    class CachedStatic(StaticFiles):
        def file_response(self, *args, **kwargs):
            resp = super().file_response(*args, **kwargs)
            immutable = "public, max-age=31536000, immutable" if "/assets/" in str(
                getattr(resp, "path", "")
            ) else "no-cache"
            resp.headers["Cache-Control"] = immutable
            return resp

    if dist.exists():
        app.mount("/", CachedStatic(directory=dist, html=True), name="web")
    return app
