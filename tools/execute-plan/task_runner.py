"""task_runner.py — 单个 Task 的执行器

用框架注入的 llm_fn 多轮对话驱动 TDD：
  写测试 → 确认 FAIL → 写实现 → 确认 PASS → commit
LLM 厂商无关，由 OpenClaw 框架决定。
"""

PATTERNS = """
## 关键模式（必须遵守）

### Lazy Import — mock 正确性
```python
import tools.foo.bar as _bar_mod
def run():
    client = _bar_mod.BarClass()  # 每次读当前命名空间，patch 生效
```

### 静态辅助方法 → 本地纯函数
不在可能被 mock 的类上调 staticmethod，直接写 _get_text(page, field) 纯函数。

### 幂等守卫
```python
if log.already_ran(task_name, run_date):
    return
```

### Fail-safe 上报
```python
try:
    # 执行逻辑
    log.record(task_name, run_date, "success")
except Exception as e:
    log.record(task_name, run_date, "failed", detail=str(e))
    # 通过框架消息接口通知负责人
```

### 敏感消息草稿优先
不直接发给终端用户，先发草稿给创始人/管理员确认。
"""


def run_task(task, plan_path: str, project_context: str, llm_fn) -> dict:
    """
    执行单个 task。
    llm_fn(messages) -> str  — 框架注入，平台/厂商无关。
    返回: {"status": "DONE"|"DONE_WITH_CONCERNS"|"BLOCKED", "detail": str, "concerns": str}
    """
    system_context = f"""你是一个专业开发者，正在实现项目中的单个任务。

## 项目上下文
{project_context}

{PATTERNS}

## 规则
1. 先写测试（TDD），运行确认 FAIL
2. 写实现，运行确认 PASS
3. 写 skill config（如有）
4. git add [只属于本任务的文件] && git commit
5. 最后一行必须是：DONE / DONE_WITH_CONCERNS / BLOCKED

只操作任务描述中明确列出的文件。"""

    messages = [
        {"role": "system", "content": system_context},
        {"role": "user", "content": task.content},
    ]

    for _ in range(12):
        response = llm_fn(messages)
        messages.append({"role": "assistant", "content": response})

        last_line = response.strip().split("\n")[-1].strip().upper()
        if last_line in ("DONE", "DONE_WITH_CONCERNS", "BLOCKED"):
            concerns = _extract_concerns(response) if last_line == "DONE_WITH_CONCERNS" else ""
            return {"status": last_line, "detail": response[-500:], "concerns": concerns}

        messages.append({
            "role": "user",
            "content": "继续执行下一步。完成后最后一行写 DONE、DONE_WITH_CONCERNS 或 BLOCKED。",
        })

    return {"status": "BLOCKED", "detail": "超过最大轮次（12轮）未完成", "concerns": ""}


def _extract_concerns(text: str) -> str:
    lines = text.split("\n")
    start = next(
        (i for i, l in enumerate(lines)
         if any(w in l.lower() for w in ["concern", "注意", "问题", "warn"])),
        len(lines) - 5,
    )
    return "\n".join(lines[start:start + 8])
