"""validator.py — 验收清单对比 + 评分"""
import json

_VALIDATOR_PROMPT = """评估执行结果是否满足验收标准。

真实意图：{true_intent}

验收清单：
{criteria_list}

执行结果汇总：
{results_summary}

对每条验收标准逐一判断，只输出 JSON：
{{
  "checklist": [
    {{"criterion": "...", "passed": true, "evidence": "引用结果中的证据"}}
  ],
  "failed_tasks": [需要重跑的 task id 整数列表，无则 []],
  "suggestion": "重跑时的改进方向（全部通过时留空字符串）"
}}"""


def compute_score(checklist: list[dict]) -> int:
    """passed 比例 × 100，四舍五入。空列表返回 0。"""
    if not checklist:
        return 0
    passed = sum(1 for c in checklist if c.get("passed", False))
    return round(passed / len(checklist) * 100)


class Validator:
    @staticmethod
    def check(plan: dict, results: dict, llm) -> dict:
        """返回 ValidationResult dict（含 score 和 passed 字段）。"""
        criteria_list = "\n".join(
            f"{i + 1}. {c}"
            for i, c in enumerate(plan["acceptance_criteria"])
        )
        results_summary = "\n".join(
            f"[子任务 {tid}: {next((t['name'] for t in plan['tasks'] if t['id'] == tid), '?')}]\n{output}"
            for tid, output in results.items()
        ) or "（无执行结果）"

        prompt = _VALIDATOR_PROMPT.format(
            true_intent=plan["true_intent"],
            criteria_list=criteria_list,
            results_summary=results_summary,
        )
        raw = llm([{"role": "user", "content": prompt}])

        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            data = {
                "checklist": [
                    {"criterion": c, "passed": False, "evidence": "解析失败"}
                    for c in plan["acceptance_criteria"]
                ],
                "failed_tasks": [t["id"] for t in plan["tasks"]],
                "suggestion": "Validator 返回无效 JSON，需重跑全部子任务",
            }

        data["score"] = compute_score(data["checklist"])
        data["passed"] = data["score"] >= 80
        return data
