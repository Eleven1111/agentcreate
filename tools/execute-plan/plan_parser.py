"""plan_parser.py — 解析 plan.md，提取 tasks 和 depends_on DAG

Plan 格式要求：
    ## Task N: [名称]
    depends_on: []           ← 无依赖
    depends_on: [1, 3, 5]   ← 依赖 Task 1、3、5
    ...（任务详细内容）
"""
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Task:
    id: int
    name: str
    depends_on: list[int]
    content: str  # 完整的任务描述文本（含测试代码、实现代码、commit 命令）


class PlanParser:
    def __init__(self, plan_path: str):
        self.plan_path = plan_path
        self._tasks: list[Task] | None = None

    @property
    def tasks(self) -> list[Task]:
        if self._tasks is None:
            self._tasks = self._parse()
        return self._tasks

    def _parse(self) -> list[Task]:
        text = Path(self.plan_path).read_text(encoding="utf-8")
        # 按 ## Task N: 分割
        sections = re.split(r"(?=^## Task \d+:)", text, flags=re.MULTILINE)
        tasks = []
        for section in sections:
            m = re.match(r"^## Task (\d+):\s*(.+)", section.strip())
            if not m:
                continue
            task_id = int(m.group(1))
            task_name = m.group(2).strip()

            # 解析 depends_on
            dep_match = re.search(r"depends_on:\s*\[([^\]]*)\]", section)
            if dep_match:
                raw = dep_match.group(1).strip()
                depends_on = [int(x.strip()) for x in raw.split(",") if x.strip().isdigit()]
            else:
                depends_on = []

            tasks.append(Task(
                id=task_id,
                name=task_name,
                depends_on=depends_on,
                content=section,
            ))
        return tasks

    def get_task(self, task_id: int) -> Task | None:
        return next((t for t in self.tasks if t.id == task_id), None)

    def get_eligible(self, completed: set[int], pending: set[int]) -> list[Task]:
        """返回所有依赖已完成且仍 pending 的任务（可立即并行执行）"""
        return [
            t for t in self.tasks
            if t.id in pending and set(t.depends_on).issubset(completed)
        ]
