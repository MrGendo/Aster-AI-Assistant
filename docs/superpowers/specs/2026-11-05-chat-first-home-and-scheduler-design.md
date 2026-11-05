# 设计：首页对话化重构 + 定时推送/闹钟

日期：2026-11-05 ｜ 状态：待批准

## 任务 1：首页改成纯对话界面

### 文档研读结论

- **Chanzhaoyu/chatgpt-web**（★31345，MIT，Vue3 同栈）：布局约定——消息流占满主区、输入框固定底部、用户/助手左右气泡、自动滚到底部、"新对话"显式入口
- **lobe-chat / open-webui**（★8.4万/15.8万）：助手回复 markdown 渲染、代码块样式、任务执行中"生成中"指示；会话有持久 id 可续聊
- 我们已有底子：conversations/messages 表、10 条窗口、markdown-it——重构主要是布局与交互形态，不新建数据

### 方案

- **布局**：首页改为全高三段式——顶部窄条（当前会话标题 + 新对话按钮）；中间滚动消息流（自动滚底）；底部固定输入区（Enter 发送 / Shift+Enter 换行）
- **气泡**：用户消息右侧主色气泡；agent 回复左侧卡片（markdown 渲染，复用 mdHtml）
- **任务型输入的呈现**：不再放 ResultView 大卡。发任务型消息后，流里出现"任务卡气泡"（状态徽标：执行中▸/等待确认⏸/完成✓/失败✗ + 目标摘要 + 结果摘要/下载按钮），点击跳任务中心看全量；审批请求也以卡片气泡内联（批准/拒绝按钮）——保持"审批不离开首页"的既定偏好
- **历史会话**：页面加载时恢复最近一个会话的消息（GET /api/chat/{cid}）；首版不做会话列表侧栏（消息在库，后续可加）
- **移除**：首页的历史任务列表、q-bubble+textarea 旧形态；任务历史只在任务中心（用户明确要求）

## 任务 3：定时推送与闹钟

### 文档研读结论（APScheduler 3.11，MIT，★7683）

读完 3.x 用户手册核心面（调度器配置/触发器/任务 CRUD/暂停恢复/关停）：
- 三类触发器：`date`（一次性）/`interval`（固定间隔）/`cron`（每日定时）——正好覆盖闹钟与定时推送两类需求
- **重启防重复**（文档明示）：持久化场景必须显式 `id` + `replace_existing=True`，否则每次重启叠加任务副本 → 我们用自有表为唯一事实源，启动时全量重注册
- 错过处理：`misfire_grace_time` + `coalesce=True`（停机错过合并跑一次，不堆积）
- MemoryJobStore 即可（表是事实源，不引入 SQLAlchemyJobStore 依赖）

### 方案

**数据**（migration 0007）：
- `schedules`：id, kind（task=定时任务 / alarm=闹钟）, goal/content, trigger_json（{type, hour, minute, interval_min, run_at}）, enabled, last_run_at, next_run_at, last_task_id, created_at
- `notifications`：id, kind（alarm/task_done）, text, ref_task_id, created_at, read

**调度器**：`server/aster/scheduler.py`——BackgroundScheduler（MemoryJobStore），启动时从 schedules 表注册（id=str(行id), replace_existing=True, misfire_grace_time=300, coalesce=True）；到点行为：
- task → create_task + execute（**照常走门禁**：L1 能力定时触发同样进审批，L2 永不自动——现有硬规则不因定时豁免）；完成后写通知
- alarm → 写 notifications（前端顶栏铃铛 badge + 下拉）

**时间语义**（自然语言 → trigger）：规则优先（每天HH:MM / 每N分钟 / N小时后 / 今天明天HH点 / @YYYY-MM-DD HH:MM），规则不中则 LLM 提取 JSON（已有 provider），都失败则要求用户明确时间。

**对话路由**：`提醒我…` 类含时间 → 闹钟；`每天/定期…` → 定时任务；无时间词维持现状（待办）。

**UI**：第 8 个 tab「定时」——添加表单（目标 + 时间语义输入）+ 列表（触发描述/下次运行/启停开关/删除/最近一次结果）；侧栏底部铃铛（未读 badge，下拉最近 20 条通知，点读标记已读）。

**不做**：cron 完整表达式输入（自然语言够用）、声音提醒（首版通知 badge；浏览器通知权限后续）、会话列表侧栏。

## 验证标准

1. 单测：时间语义解析（规则路径）、路由命中/让路、迁移
2. 调度实测：interval=30 秒的测试任务真实触发两次（截 next_run 变化为证）；闹钟 1 分钟后真实弹出
3. 首页重构：无头浏览器验证三段式布局/气泡/自动滚动/任务卡气泡/审批按钮/会话恢复
4. 既有 36 测试不回归
