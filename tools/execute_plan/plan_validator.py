"""plan_validator.py — Phase 1 计划验证

两层防护：
  1. validate_structure(tasks)             — 纯结构检查，不调 LLM
     · 任务数合理（2–25）
     · ID 连续无跳跃
     · depends_on 无循环

  2. validate_traceability(tasks, reqs, llm_fn) — 需求追溯，调一次 LLM
     · 每个任务必须能追溯到用户在 Phase 0 明确提及的需求
     · 找不到来源的任务 → 疑似 LLM 自行添加（瞎编任务）
     · 返回 covered / uncovered / reasons，由调用方决定如何处置
"""
import json
import re
from collections import defaultdict


# ── 1. 结构检查 ────────────────────────────────────────────────────────────────

def validate_structure(tasks: list) -> list[str]:
    """
    纯确定性检查，返回问题列表。空列表 = 结构正常。
    tasks: list[Task]（含 .id, .depends_on）
    """
    issues: list[str] = []

    if len(tasks) < 2:
        issues.append(f"任务数太少（{len(tasks)} 个），计划疑似不完整")
    if len(tasks) > 25:
        issues.append(f"任务数过多（{len(tasks)} 个），计划疑似发散")

    ids = [t.id for t in tasks]
    expected = list(range(1, len(tasks) + 1))
    if sorted(ids) != expected:
        issues.append(f"任务 ID 不连续：{sorted(ids)}，期望 {expected}")

    cycles = _find_cycles(tasks)
    if cycles:
        issues.append(f"depends_on 存在循环依赖：{cycles}")

    return issues


def _find_cycles(tasks: list) -> list[list[int]]:
    """Kahn 算法检测环，返回所有参与循环的任务 ID 列表。"""
    graph: dict[int, list[int]] = defaultdict(list)
    in_degree: dict[int, int] = {t.id: 0 for t in tasks}

    for t in tasks:
        for dep in t.depends_on:
            graph[dep].append(t.id)
            in_degree[t.id] += 1

    queue = [tid for tid, deg in in_degree.items() if deg == 0]
    visited = 0
    while queue:
        node = queue.pop()
        visited += 1
        for neighbor in graph[node]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if visited == len(tasks):
        return []
    return [tid for tid, deg in in_degree.items() if deg > 0]


# ── 2. 需求追溯 ────────────────────────────────────────────────────────────────

def validate_traceability(
    tasks: list,
    requirements_text: str,
    llm_fn,
) -> dict:
    """
    调用 llm_fn 检查每个任务是否有需求来源。

    返回：
    {
        "covered":   [int, ...],   # 有需求支撑的任务 ID
        "uncovered": [int, ...],   # 找不到需求来源的任务 ID
        "reasons":   {str: str},   # {任务ID字符串: 说明}
    }
    出错时保守策略：全部标记为 covered，不阻断执行。
    """
    task_lines = "\n".join(f"Task {t.id}: {t.name}" for t in tasks)
    all_ids = [t.id for t in tasks]

    prompt = f"""你是一个需求追溯审查员。

## 用户在 Phase 0 确认的需求
{requirements_text}

## 计划中的任务列表
{task_lines}

## 审查规则
- 每个任务必须服务于用户明确提出的需求
- 用户从未提及的功能不应出现（"镀金"任务）
- 基础设施任务（测试框架、CI、日志）只要合理支撑上层需求即视为 covered

以 JSON 格式输出，**仅输出 JSON，不要其他文字**：
{{
  "covered": [有需求来源的任务 ID 列表，整数],
  "uncovered": [找不到需求来源的任务 ID 列表，整数],
  "reasons": {{"任务ID字符串": "为何认为无需求来源（一句话）"}}
}}"""

    try:
        response = llm_fn([{"role": "user", "content": prompt}])
        return _parse_traceability(response, all_ids)
    except Exception:
        return _safe_all_covered(all_ids)


def _parse_traceability(response: str, all_ids: list[int]) -> dict:
    m = re.search(r"\{.*\}", response, re.DOTALL)
    if not m:
        return _safe_all_covered(all_ids)
    try:
        data = json.loads(m.group())
        covered = [int(x) for x in data.get("covered", [])]
        uncovered = [int(x) for x in data.get("uncovered", [])]
        reasons = {str(k): str(v) for k, v in data.get("reasons", {}).items()}

        # 防御：LLM 漏分类的 ID 归入 covered
        classified = set(covered) | set(uncovered)
        for tid in all_ids:
            if tid not in classified:
                covered.append(tid)

        return {"covered": covered, "uncovered": uncovered, "reasons": reasons}
    except (json.JSONDecodeError, ValueError):
        return _safe_all_covered(all_ids)


def _safe_all_covered(all_ids: list[int]) -> dict:
    return {"covered": list(all_ids), "uncovered": [], "reasons": {}}
