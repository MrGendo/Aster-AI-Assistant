# FastAPI 应用：REST + SSE（任务进度轮询推送）
from ..db import connect, migrate
from ..gateway import core as gateway
from ..providers import router as provider
from ..runtime import registry
from . import caps, discovery, memories, system, tasks


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
        conn.close()
        from ..watch import loop as watch_loop
        watch_loop.start()  # Stage 2：watchlist 后台巡检

    app.include_router(tasks.router, prefix="/api")
    app.include_router(caps.router, prefix="/api")
    app.include_router(memories.router, prefix="/api")
    app.include_router(system.router, prefix="/api")
    app.include_router(discovery.router, prefix="/api")

    # 前端产物：构建后由服务直接托管（API 路由先注册，静态挂载兜底其余路径）
    from fastapi.staticfiles import StaticFiles
    from ..config import ROOT
    dist = ROOT / "web" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")
    return app
