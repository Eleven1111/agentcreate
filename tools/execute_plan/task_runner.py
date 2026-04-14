"""task_runner.py — 单个 Task 的执行器

用框架注入的 llm_fn 多轮对话驱动 TDD：
  写测试 → 确认 FAIL → 写实现 → 确认 PASS → commit
LLM 厂商无关，由 OpenClaw 框架决定。

防漂移三件套：
  1. 命令拦截器  — LLM 输出里的 shell 命令由 subprocess 真实执行，输出注入回上下文
  2. 重锚机制    — 每 REANCHOR_EVERY 轮把原始任务 spec 重新注入 user 消息
  3. 循环检测    — 相邻两轮相似度 ≥ 72% 时换提示语打断
"""
from .cmd_interceptor import extract_commands, run_commands, is_circular

_REANCHOR_EVERY = 4  # 每 N 轮重注入一次原始任务 spec

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

    task_spec = task.content  # 保存原始 spec，用于重锚

    messages = [
        {"role": "system", "content": system_context},
        {"role": "user", "content": task_spec},
    ]

    prev_response = ""
    for round_num in range(12):
        response = llm_fn(messages)
        messages.append({"role": "assistant", "content": response})

        last_line = response.strip().split("\n")[-1].strip().upper()
        if last_line in ("DONE", "DONE_WITH_CONCERNS", "BLOCKED"):
            concerns = _extract_concerns(response) if last_line == "DONE_WITH_CONCERNS" else ""
            return {"status": last_line, "detail": response[-500:], "concerns": concerns}

        # ── 1. 命令拦截：真实执行，注入真实输出 ──────────────────────────────
        commands = extract_commands(response)
        if commands:
            cmd_results = run_commands(commands)
            output_lines = []
            for r in cmd_results:
                output_lines.append(f"$ {r['cmd']}")
                if r["stdout"]:
                    output_lines.append(r["stdout"][:1500])
                if r["stderr"]:
                    output_lines.append(f"[stderr]: {r['stderr'][:400]}")
                if r["returncode"] != 0:
                    output_lines.append(f"[exit code: {r['returncode']}]")
            next_msg = (
                "命令执行结果（真实 subprocess 输出，非模拟）：\n"
                + "\n".join(output_lines)
                + "\n\n继续下一步。完成后最后一行写 DONE、DONE_WITH_CONCERNS 或 BLOCKED。"
            )
        else:
            next_msg = "继续执行下一步。完成后最后一行写 DONE、DONE_WITH_CONCERNS 或 BLOCKED。"

        # ── 2. 重锚：每 REANCHOR_EVERY 轮重注入原始任务 spec ─────────────────
        if round_num > 0 and (round_num + 1) % _REANCHOR_EVERY == 0:
            next_msg = (
                f"【任务重确认 — 第 {round_num + 1} 轮】\n"
                f"原始任务要求：\n{task_spec[:600]}\n\n"
                + next_msg
            )

        # ── 3. 循环检测：相似度过高时换提示打断 ──────────────────────────────
        if is_circular(prev_response, response):
            next_msg = (
                "⚠️ 检测到重复输出，请换一个方向推进，不要重复之前说过的内容。"
                "如果真的卡住了请写 BLOCKED 并说明具体原因。\n\n"
                + next_msg
            )

        prev_response = response
        messages.append({"role": "user", "content": next_msg})

    return {"status": "BLOCKED", "detail": "超过最大轮次（12轮）未完成", "concerns": ""}


def _extract_concerns(text: str) -> str:
    lines = text.split("\n")
    start = next(
        (i for i, l in enumerate(lines)
         if any(w in l.lower() for w in ["concern", "注意", "问题", "warn"])),
        len(lines) - 5,
    )
    return "\n".join(lines[start:start + 8])
