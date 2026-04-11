"""executor.py — 顺序子任务执行器"""

_TASK_PROMPT = """你是一个专注执行单一子任务的助手。

整体目标：{true_intent}

已完成的子任务结果：
{previous_results}

当前子任务：{task_name}
执行指令：{instruction}

直接输出执行结果，不要重复目标描述。"""


class Executor:
    @staticmethod
    def run_task(task: dict, plan: dict, results: dict, llm) -> str:
        """执行单个子任务，返回输出字符串。LLM 异常时返回 "FAILED"。"""
        prev = "\n".join(
            f"[子任务 {tid}] {output}"
            for tid, output in results.items()
            if output != "FAILED"
        ) or "（无）"

        prompt = _TASK_PROMPT.format(
            true_intent=plan["true_intent"],
            previous_results=prev,
            task_name=task["name"],
            instruction=task["instruction"],
        )
        try:
            return llm([{"role": "user", "content": prompt}])
        except Exception:
            return "FAILED"
