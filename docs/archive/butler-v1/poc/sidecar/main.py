# PoC-2：核心服务最小骨架
# 验证点：stdin 一次性 token 握手 / /health 端点 / 无 token 请求 403 / CORS（webview 前端跨源调用）
import sys
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

START = time.time()

app = FastAPI(title="aster-core-poc")

# webview 前端（dev 模式 http://localhost:1420）跨源访问本服务
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:1420", "tauri://localhost", "http://tauri.localhost"],
    allow_headers=["*"],
)


@app.middleware("http")
async def check_token(request: Request, call_next):
    """启动 token 校验：不匹配一律 403（PoC 简化为全端点校验，含 /health）"""
    if request.headers.get("x-aster-token") != TOKEN:
        return JSONResponse({"error": "invalid token"}, status_code=403)
    return await call_next(request)


@app.get("/health")
async def health():
    return {"ok": True, "uptime": round(time.time() - START, 1)}


if __name__ == "__main__":
    # 启动握手：从 stdin 读一行一次性 token，随后 stdin 不再使用
    TOKEN = sys.stdin.readline().strip()
    if not TOKEN:
        print("[core] 未收到 token，拒绝启动", file=sys.stderr)
        sys.exit(1)
    print(f"[core] token 握手成功（尾 6 位 …{TOKEN[-6:]}），开始监听 127.0.0.1:18321", file=sys.stderr)
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=18321, log_level="warning")
