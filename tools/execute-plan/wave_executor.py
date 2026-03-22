"""wave_executor.py — 并行波次执行器

用 ThreadPoolExecutor 并发跑同一波次的所有 tasks。
每个 task 在独立线程里调用 task_runner，
LLM 客户端由框架注入（ctx.llm），不自己初始化。
"""
from concurrent.futures import ThreadPoolExecutor, as_completed

from .task_runner import run_task


class WaveExecutor:
    def __init__(self, llm_fn):
        """
        llm_fn: OpenClaw 框架注入的 LLM 调用函数
                签名: llm_fn(messages: list[dict]) -> str
        """
        self.llm = llm_fn

    def run(self, tasks: list, plan_path: str, project_context: str) -> dict[int, dict]:
        """
        并发执行 tasks，返回 {task_id: result_dict}
        result_dict: {"status": str, "detail": str, "concerns": str}
        """
        results = {}
        with ThreadPoolExecutor(max_workers=min(len(tasks), 8)) as executor:
            futures = {
                executor.submit(run_task, task, plan_path, project_context, self.llm): task
                for task in tasks
            }
            for future in as_completed(futures):
                task = futures[future]
                try:
                    results[task.id] = future.result()
                except Exception as e:
                    results[task.id] = {
                        "status": "BLOCKED",
                        "detail": f"执行异常：{e}",
                        "concerns": "",
                    }
        return results
