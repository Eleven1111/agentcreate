import json
import pytest
from tools.smart_router.planner import Planner

_VALID_PLAN = {
    "raw_request": "帮我分析竞品",
    "true_intent": "了解竞争格局以制定差异化定价策略",
    "acceptance_criteria": ["包含 3 家竞品对比", "给出定价建议"],
    "tasks": [
        {"id": 1, "name": "竞品数据收集", "instruction": "列出主要竞品及其价格"},
        {"id": 2, "name": "定价建议", "instruction": "基于竞品数据给出定价区间"},
    ],
}


def make_llm(response):
    def llm(messages):
        return response
    return llm


class TestPlannerExtract:
    def test_valid_json_returns_plan(self):
        llm = make_llm(json.dumps(_VALID_PLAN))
        plan = Planner.extract("帮我分析竞品", llm)
        assert plan["true_intent"] == "了解竞争格局以制定差异化定价策略"
        assert len(plan["tasks"]) == 2
        assert plan["tasks"][0]["id"] == 1

    def test_raw_request_is_preserved(self):
        llm = make_llm(json.dumps(_VALID_PLAN))
        plan = Planner.extract("帮我分析竞品", llm)
        assert plan["raw_request"] == "帮我分析竞品"

    def test_raw_request_fallback_when_missing(self):
        """LLM 返回的 JSON 没有 raw_request 时，用原始消息补全。"""
        plan_without_raw = {k: v for k, v in _VALID_PLAN.items() if k != "raw_request"}
        llm = make_llm(json.dumps(plan_without_raw))
        plan = Planner.extract("原始消息", llm)
        assert plan["raw_request"] == "原始消息"

    def test_invalid_json_raises_value_error(self):
        llm = make_llm("这不是 JSON")
        with pytest.raises(ValueError, match="无效 JSON"):
            Planner.extract("任意消息", llm)

    def test_llm_returns_none_raises_value_error(self):
        llm = make_llm(None)
        with pytest.raises(ValueError, match="无效 JSON"):
            Planner.extract("任意消息", llm)

    def test_prompt_contains_message(self):
        captured = []
        def llm(messages):
            captured.append(messages[0]["content"])
            return json.dumps(_VALID_PLAN)
        Planner.extract("特定消息内容ABC", llm)
        assert "特定消息内容ABC" in captured[0]
