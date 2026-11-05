# 执行计划：首页对话化 + 定时推送/闹钟

设计文档：`docs/superpowers/specs/2026-11-05-chat-first-home-and-scheduler-design.md`

## 任务清单

- [ ] 1. migration 0007：schedules + notifications 表
- [ ] 2. server/aster/scheduler.py：APScheduler 封装（启动重注册/启停/触发回调走门禁）+ 时间语义解析（规则优先，LLM 兜底）
- [ ] 3. API：schedules CRUD + notifications 列表/已读
- [ ] 4. 对话路由：含时间的"提醒我"→alarm；"每天/定期"→task；无时间→待办不变
- [ ] 5. 首页重构：三段式对话布局（消息流+底部输入+自动滚底）、任务卡气泡、审批气泡、会话恢复
- [ ] 6. 「定时」tab + 侧栏铃铛通知
- [ ] 7. 单测：时间解析/路由/迁移
- [ ] 8. 调度实测：30 秒 interval 任务真实触发两次；1 分钟闹钟真实弹出
- [ ] 9. 无头浏览器验证首页新布局
- [ ] 10. 任务日志归档 + 提交

## 回滚

git revert；调度器独立模块可直接停用。
