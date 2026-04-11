# Smart Router — 设计文档

**日期：** 2026-04-10  
**项目：** OpenClaw  
**状态：** 已确认，待实施

---

## 概述

`smart-router` 是 OpenClaw 主流程的前置拦截层。所有进入 OpenClaw 的消息先经过这里：

- **简单请求** → 单次 LLM 调用直接回答（透传）
- **复杂请求** → 意图分析 → 任务拆解 → 顺序执行 → 对齐验收 → 交付

触发优先级设为 `lowest`，确保精确 skill（如 `execute-plan`）先匹配，兜底才走智能路由。  
⚠️ `priority: lowest` 需在实施时确认 OpenClaw trigger 字段的合法值，备选方案：`priority: 0` 或 `fallback: true`。

---

## 架构

### 文件结构

```
agentcreate/
└── skills/
    └── smart-router/
        ├── smart-router.md      # skill 描述符
        └── smart_router.py      # 主入口 handle_message

└── tools/
    └── smart-router/
        ├── __init__.py
        ├── gate.py              # Gate.classify()
        ├── planner.py           # Planner.extract()
        ├── executor.py          # Executor.run_task()
        └── validator.py         # Validator.check() + compute_score()
```

`state_manager` 复用 `tools/execute_plan/state_manager.py`，无需新建。

### 调用链

```
handle_message(text, user_id, ctx)
    ↓
Gate.classify() — 1次 LLM 调用
    → "simple"  → ctx.llm(直接回答) → ctx.reply() → 结束
    → "complex" → 进入状态机

Planner.extract(text) → IntentPlan
Executor.run_task() × N → results
Validator.check(plan, results) → ValidationResult
    → score ≥ 80 → _merge_results(results) → ctx.reply(final_answer)
    → score < 80 → 重跑 failed_tasks（最多 2 次）→ 再次 Validator

# _merge_results：按 task 顺序拼接各子任务输出，加标题分隔，返回 str
```

### 状态机

```
idle → executing → validating → done
           ↑            ↓（score < 80，retries < 2）
           └────────────┘（重跑 failed_tasks）
```

注：Planner 在 `idle` 阶段内联执行，不产生独立的 `analyzing` 状态。

---

## 数据结构

### IntentPlan

```python
{
    "raw_request": "用户原始消息",
    "true_intent": "用户真正想解决的问题（一句话）",
    "acceptance_criteria": [
        "交付物包含 X",
        "回答了 Y 问题",
        "格式满足 Z 要求"
    ],
    "tasks": [
        {"id": 1, "name": "子任务名称", "instruction": "给 LLM 的执行指令（含上下文）"},
        {"id": 2, "name": "...", "instruction": "..."}
    ]
}
```

### ValidationResult

```python
{
    "checklist": [
        {"criterion": "交付物包含 X", "passed": True,  "evidence": "..."},
        {"criterion": "回答了 Y 问题",  "passed": False, "evidence": "..."}
    ],
    "score": 72,
    "passed": False,
    "failed_tasks": [1],
    "suggestion": "第1项子任务需要补充..."
}
```

---

## 模块设计

### Gate（gate.py）

单次 LLM 调用，二分类：

```
GATE_PROMPT:
判断复杂度 → "simple" | "complex"
输出 JSON: {"complexity": "simple"|"complex", "reason": "一句话原因"}
```

规则：
- `simple`：单步可答，无需拆解（问候、查询单一事实、直接指令）
- `complex`：需要多步推理、跨领域协调、或意图不明确

### Planner（planner.py）

单次 LLM 调用，合并意图提取 + 任务拆解：

```
PLANNER_PROMPT:
1. 识别"表面需求"vs"真实意图"的差距
2. 生成 2-5 个子任务（instruction 字段主动注入上下文）
3. 生成 3-5 条具体可核查的验收清单
输出 IntentPlan JSON
```

约束：子任务上限 5 个——更多说明任务需要先澄清。

### Executor（executor.py）

顺序执行，每个子任务独立一次 `ctx.llm`：

```
TASK_PROMPT:
整体目标 + 已完成结果（滚动传入） + 当前子任务指令
```

- 顺序而非并行——路由层子任务通常有逻辑依赖
- `previous_results` 滚动传入，保证上下文连贯
- 执行失败标记 `"FAILED"`，进 `failed_tasks`

### Validator（validator.py）

```
VALIDATOR_PROMPT:
逐条核查 acceptance_criteria → checklist + failed_tasks + suggestion
```

评分公式（硬编码，不依赖 LLM，避免打分漂移）：

```python
def compute_score(checklist):
    passed = sum(1 for c in checklist if c["passed"])
    return round(passed / len(checklist) * 100)
```

阈值：`score >= 80` 视为通过。

重试逻辑：
- 最多重试 `MAX_RETRIES = 2` 次
- 只重跑 `failed_tasks`，不重跑全部
- 重跑的 instruction 追加 `suggestion` 内容

---

## Skill 描述符

```yaml
---
name: smart-router
description: OpenClaw 智能路由层。所有消息的前置拦截器——自动判断复杂度，复杂请求做意图分析+任务拆解+多步执行+对齐验收，简单请求直接透传。
triggers:
  - type: message
    priority: lowest
    match: "*"
entrypoint: skills/smart_router/smart_router.py::handle_message
---
```

---

## 边界与约束

| 项目 | 决策 |
|------|------|
| 子任务上限 | 5 个（更多 → 要求先澄清需求） |
| 最大重试次数 | 2 次 |
| 验收通过阈值 | score ≥ 80 |
| 执行顺序 | 顺序（非并行） |
| 评分方式 | 硬编码公式，LLM 仅判断 passed/failed |
| 状态持久化 | 复用 execute-plan 的 state_manager |
| 触发优先级 | lowest（精确 skill 优先） |

---

## 不在范围内

- 多用户并发隔离（由 state_manager 的 user_id 键保证，无需额外处理）
- 子任务并行执行（有意设计为顺序）
- 动态调整阈值（固定 80 分，后续可配置化）
