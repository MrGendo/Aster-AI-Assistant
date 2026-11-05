---
name: feedback-doc-first-integration
description: 引入/学习任何 GitHub 项目前必须先完整通读其文档，理解优劣与流程后才能动手集成
metadata:
  type: feedback
---

用户明确要求（2026-11-05）：凡是学习 GitHub 项目来完成进化、添加新功能，必须先把它文档完整过一遍，理解透彻项目的优势、功能、缺点、流程，才能开始集成。

**Why:** vue-flow 接入时三次返工（箭头默认色不显、依赖提取空边、自定义节点缺 Handle）全是文档里写明的基础用法——半吊子理解直接动手，把试错成本转嫁给用户。这与平台自身的评估理念一致（PRD §5.6 把"项目自述"列为最低证据级，集成前应升到"理解透彻"级）。

**How to apply:** 接入任何外部依赖（库/框架/CLI/项目）时的固定顺序：①官方文档完整通读（含 concepts/guides/API，不能只看 quickstart）②列出优势/短板/约定与陷阱 ③写清接入方案再动手 ④集成后在任务日志记录"文档里早写了"的返工项作为自查。排队中的 gpt-researcher（候选#13）即适用：动手前先读透 docs.gpt-researcher.dev。相关 [[project-aster-status]]。
