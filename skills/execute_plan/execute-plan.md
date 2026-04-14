---
name: execute-plan
description: 端到端 AI 辅助开发：需求对齐 → 生成计划 → 并行实现 → 验收打标。通过消息平台全程与用户交互，支持断点续跑。
triggers:
  - type: message
    keywords: ["执行计划", "开始开发", "/execute-plan", "execute plan"]
    priority: high
  - type: message
    keywords: ["重置开发", "reset execute-plan"]
    priority: high
entrypoint: skills/execute_plan/execute_plan.py::handle_message
---

执行完整项目开发流程：
- Phase 0: 需求对齐（问答 → 3方案比较 → 确认）
- Phase 1: 自动生成 plan.md（含 depends_on 依赖声明）
- Phase 2: 拓扑调度并行执行（所有无依赖任务同时启动，不区分"期"）
- Phase 3: 全量测试 + 覆盖率 ≥ 80% + git tag

会话中断后重新触发可自动恢复到断点。
