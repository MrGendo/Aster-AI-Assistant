# 设计：待办管理功能

日期：2026-11-05 ｜ 状态：待批准

## 一、文档研读结论（doc-first 关卡产出）

**读了什么**：todotxt/todo.txt 规范全文（★3441，含完成态规则）+ GothenburgBitFactory/taskwarrior（★6154 MIT）man 手册的 due/blocked/ready/urgency 模型。筛掉的：vikunja（AGPL，全功能项目管理，对聊天优先的本地平台是过度工程）、planify/calcure（GUI 应用无可借鉴的数据模型）。

**todo.txt 可学（哲学与格式）**：
- 三轴切片：优先级 `(A)` / 项目 `+proj` / 场景 `@ctx`；一行一事，非结构化正文靠搜索切片
- 人的可读性优先：不加工具也能看懂；完成态 `x 日期` 排序沉底
- **教训**：字段无限自由是双刃剑——我们只保留 priority + due 两个结构字段，其余归自由文本（最简安全）

**Taskwarrior 可学（调度语义）**：
- due 临近/逾期驱动排序（overdue > today > tomorrow > none）
- 依赖阻塞：blocked / ready 状态区分
- urgency 是**算出来的**排序值而非用户手填——排序逻辑收在系统里
- 简化取舍：依赖阻塞首版不做（Aster 待办间依赖是伪需求）；排序公式取"逾期>今日>优先级>创建序"

**Aster 独有增量（无外部可学）**：待办 ↔ agent 任务打通——待办可"转任务"让 agent 执行；对话里自然语言增查待办。

## 二、方案

**数据**（新表 todos，migration 0006）：
`id, content(自由文本), priority(TEXT A-Z 可空), due(TEXT YYYY-MM-DD 可空), done(0/1), created_at, done_at, task_id(可空 FK→tasks，转执行后回链), source(user/agent)`

**排序**（学 taskwarrior 简化）：`逾期 > 今日到期 > 未来到期 > 无日期`，同级按 priority(A 先) → created_at。

**API**：`GET/POST /api/todos`、`PATCH /api/todos/{id}`（勾选/编辑/转执行）、`DELETE /api/todos/{id}`。转执行 = 后端代发 chat(goal=content)，回写 task_id。

**对话路由**（classify，与 mem0 同级）：
- `^(待办|todo)：X` 或 `添加待办 X` → 新增（解析 `!A`/`(A)` 优先级与 `@2026-11-08`/`截止YYYY-MM-DD` 日期——正则抽取，抽不到不强求）
- `看看待办|待办列表|我的待办` → 列出（pending 前 done 后，各取前 5）
- 完成勾选走 UI 点击（对话里"完成待办N"易错位，不做）

**UI**：第 7 个 tab「待办」——输入框（支持优先级/日期语法）+ 列表（勾选/删除/编辑/转任务按钮，转后显示任务链接）+ 顶部过滤（全部/进行中/已完成 + 关键词搜索）。首页不加区块（保持单一战场，用户既定偏好）。

**不做的**：子任务层级、项目/场景独立字段（自由文本+搜索足够，todo.txt 哲学）、待办间依赖、重复待办、通知提醒。

## 三、验证标准

1. 单测：优先级/日期解析、排序序、路由命中/让路、迁移
2. 端到端：对话添加 → UI 勾选/编辑 → 转任务执行 → 回链展示
3. 27+ 既有测试不回归
