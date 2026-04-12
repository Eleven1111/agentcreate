"""planner.py — 意图提取 + 任务拆解"""
import json

_PLANNER_PROMPT = """分析用户请求，提取真实意图并拆解为可执行子任务。

用户请求：
{message}

步骤：
1. 识别用户"表面需求"和"真实意图"的差距
2. 根据真实意图生成 2-5 个子任务（不要过度拆分）
3. 生成 3-5 条具体可核查的验收清单

只输出 JSON：
{{
  "raw_request": "（复制用户原始消息）",
  "true_intent": "用户真正想解决的根本问题（一句话）",
  "acceptance_criteria": ["具体可核查的验收条件1", "验收条件2"],
  "tasks": [
    {{"id": 1, "name": "子任务名称", "instruction": "给执行者的详细指令，包含足够上下文"}},
    {{"id": 2, "name": "...", "instruction": "..."}}
  ]
}}"""


class Planner:
    @staticmethod
    def extract(message: str, llm) -> dict:
        """返回 IntentPlan dict。解析失败时抛出 ValueError。"""
        prompt = _PLANNER_PROMPT.format(message=message)
        raw = llm([{"role": "user", "content": prompt}])
        try:
            plan = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as e:
            raise ValueError(f"Planner LLM 返回无效 JSON: {e}\nRaw: {str(raw)[:200]}")
        plan.setdefault("raw_request", message)

        required = {"true_intent", "acceptance_criteria", "tasks"}
        missing = required - plan.keys()
        if missing:
            raise ValueError(f"Planner LLM 返回 JSON 缺少必要字段：{missing}")
        if not isinstance(plan.get("tasks"), list) or len(plan["tasks"]) == 0:
            raise ValueError("Planner tasks 字段必须是非空列表")

        return plan
