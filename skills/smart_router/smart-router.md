---
name: smart-router
description: OpenClaw 智能路由层。所有消息的前置拦截器——自动判断复杂度，复杂请求做意图分析+任务拆解+多步执行+对齐验收，简单请求直接透传。输入「重置路由」可清除当前用户状态。
triggers:
  - type: message
    priority: lowest
    match: "*"
entrypoint: skills/smart_router/smart_router.py::handle_message
---

## 功能说明

`smart-router` 是 OpenClaw 的前置拦截层，触发优先级最低（`priority: lowest`），确保精确 skill 先匹配，兜底才走此路由。

### 处理流程

**简单请求**（问候、单一事实查询）：
```
消息 → Gate 判断 simple → 直接 LLM 回答 → 返回
```

**复杂请求**（多步推理、意图模糊）：
```
消息 → Gate 判断 complex
     → Planner 提取真实意图 + 拆解 2-5 个子任务 + 生成验收清单
     → Executor 顺序执行各子任务（前序结果滚动传入）
     → Validator 清单核查 + 评分（满分100）
          ≥ 80分 → 交付结果
          < 80分 → 重跑失败子任务（最多重试 2 次）→ 交付
```

### 状态持久化

对话状态存储在 `~/.openclaw/smart-router/{user_id}.json`，支持断点续跑。

### 重置

发送「重置路由」清除当前用户的对话状态。
