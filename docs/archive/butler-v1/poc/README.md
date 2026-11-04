# Phase 0 PoC 结论汇总

- 日期：2026-11-03
- 执行状态：PoC-2/3/4 全部通过；PoC-1/7 机器可验部分通过、目测项待用户确认；PoC-5/6 素材就绪，分别待费用确认与语雀 Token

## 结果一览

| PoC | 结论 | 关键数据 |
|---|---|---|
| 1 悬浮球 | ✅ 机器可验部分全过（编译/双窗口/拖动API/穿透API/六态轮播已接线）；**透明无白底、60fps 拖动、穿透效果待目测** | 应用运行中（tauri dev） |
| 2 sidecar 生命周期 | ✅ **全过** | 无 token → 403；kill -9 后 ~1s 恢复监听；LSUIElement 生效（`ApplicationType="UIElement"`，无 Dock 图标） |
| 3 FTS5 中文 | ✅ **定案配方 10/10 全对，P@5=0.95** | 见下"分词配方" |
| 4 sqlite-vec + bge | ✅ **全过** | 热加载 2.6s；编码 3282 条/s（千条 0.3s）；KNN avg 0.6ms；语义 top1 命中 20/20；峰值内存 498MB |
| 5 Claude headless | ✅ **七项全过**（实际后端 glm-5.3，见下方详注） | readonly+git干净 ✅；json-schema ✅（参数须传 JSON 字符串本体）；write 全链路 ✅（需绕行宿主 SDD 规则）；SIGTERM→143 ✅；total_cost_usd ✅（readonly $0.409 / write $0.218）；session_id ✅；--resume 上下文延续 ✅ |
| 6 语雀 MCP | ❌ **阻断（账号权限，非技术）**：官方 MCP/API 需语雀超级会员，用户确认无此会员（2026-11-03）。语雀集成整体降级为"可选，待会员变更后启用"；适配器接口保留不删 | — |
| 7 通知/自启 | ◐ LSUIElement 已验（见 PoC-2）；通知/自启按钮已上线待点击记录；dev 模式自启注册的是调试二进制，**打包后需复验** | — |

## PoC-3 定案：中文分词配方

- ❌ unicode61 裸文本：中文基本不可检索（recall 0.10）
- ❌ 纯 trigram：短语语义过严（跨词边界如 "token认证" 匹配不上原文 "Token 认证"）、<3 字查询物理不可查
- ✅ **jieba 双模式（cut + cut_for_search）并集入索引 + 查询侧双模式并集 OR 组合、靠 BM25 排序**：10/10，P@5=0.95
- 深因：jieba 对查询与文档两侧的分词粒度可能不一致（案例："MPS 后端" 在查询侧被拆成 后/端，文档侧是 后端），短语语义一票否决；OR + BM25 排序免疫此问题
- 产品建议：BM25 路 = jieba 双模式配方；trigram 不单独成路（向量路负责语义、BM25 负责关键词，已够）

## PoC-5 关键发现（ClaudeCodeAdapter 设计输入）

1. **宿主规则继承（最重要）**：headless 任务会加载用户全局 `~/.claude/CLAUDE.md`。首次 write 测试中，模型严格遵循其中的 SDD 规约——在 testrepo 里创建了 `docs/superpowers/` 的 spec/plan 然后停在审批关卡请求批准，**没有执行任何写入**（$0.218/5轮/exit 0）。程序化派发必须隔离宿主规则：本 PoC 用提示词绕行验证通过；正式方案（专用工作区 CLAUDE.md 覆盖 / scoped 环境变量）列为 Phase 1/2 调研项
2. **stdout 多行噪声**：结果 JSON 之前可能混入 `[claude-code:unrecognized_model] {...}` 等告警行（GLM 后端触发）；解析须取**最后一个完整 JSON 对象**
3. **GLM Anthropic 兼容后端全程可用**：`--output-format json`/`--json-schema`/`--resume`/费用字段均正常（后端实际模型 glm-5.3，费用由兼容层上报）。适配器不假定 Anthropic 官方后端
4. `--json-schema` 传参是 **JSON 字符串本体**，不是文件路径（传路径报 `Unrecognized token '/'`）

## 复现方式

1. **venv 符号链接陷阱**：Rust 侧 `canonicalize` 把 `.venv/bin/python` 解析成基础解释器，丢失 venv site-packages → 服务 crash-loop。修正：python 路径不做 canonicalize（lib.rs 已注释说明）
2. **通知插件 API 名**：JS 侧是 `sendNotification` 而非 `send`
3. **torch 基线内存 441MB**：仅 import 即占——产品中 embedding 组件必须**独立进程或按需加载**，否则核心服务内存预算（NFR-2 <500MB）被吃光
4. **实现偏差声明**：PoC-2 用 `std::process` 拉起 Python 而非 tauri-plugin-shell sidecar（dev 期路径解析更稳）；正式打包时改用 sidecar 声明，生命周期逻辑不变
5. macOS `ru_maxrss` 单位是字节（Linux 是 KB），基准脚本已按平台换算

## 复现方式

```bash
# PoC-2/1/7 应用（当前运行中）
cd poc/floating-ball && pnpm tauri dev
# PoC-3
poc/.venv/bin/python poc/fts5/bench.py
# PoC-4
poc/.venv/bin/python poc/vec/bench.py
# PoC-5（待批准后）
bash poc/cc-headless/run.sh
# PoC-6（待 Token 后）
YUQUE_PERSONAL_TOKEN=xxx poc/.venv/bin/python poc/yuque-mcp/probe.py
```
