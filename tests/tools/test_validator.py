import json
import pytest
from tools.smart_router.validator import Validator, compute_score

_PLAN = {
    "true_intent": "了解竞争格局",
    "acceptance_criteria": ["包含竞品对比", "给出定价建议", "有具体数字"],
    "tasks": [
        {"id": 1, "name": "竞品收集", "instruction": "列出竞品"},
        {"id": 2, "name": "定价建议", "instruction": "给出定价区间"},
    ],
}

_ALL_PASS = json.dumps({
    "checklist": [
        {"criterion": "包含竞品对比", "passed": True, "evidence": "列出了3家"},
        {"criterion": "给出定价建议", "passed": True, "evidence": "建议$12"},
        {"criterion": "有具体数字",   "passed": True, "evidence": "有$12"},
    ],
    "failed_tasks": [],
    "suggestion": "",
})

_PARTIAL_PASS = json.dumps({
    "checklist": [
        {"criterion": "包含竞品对比", "passed": True,  "evidence": "有"},
        {"criterion": "给出定价建议", "passed": False, "evidence": "缺少"},
        {"criterion": "有具体数字",   "passed": False, "evidence": "无"},
    ],
    "failed_tasks": [2],
    "suggestion": "需要补充具体定价数字",
})


def make_llm(response: str):
    def llm(messages):
        return response
    return llm


class TestComputeScore:
    def test_all_passed(self):
        checklist = [{"passed": True}, {"passed": True}, {"passed": True}]
        assert compute_score(checklist) == 100

    def test_none_passed(self):
        checklist = [{"passed": False}, {"passed": False}]
        assert compute_score(checklist) == 0

    def test_partial(self):
        checklist = [{"passed": True}, {"passed": False}, {"passed": False}]
        assert compute_score(checklist) == 33

    def test_empty_returns_zero(self):
        assert compute_score([]) == 0


class TestValidatorCheck:
    def test_all_pass_score_100(self):
        llm = make_llm(_ALL_PASS)
        result = Validator.check(_PLAN, {1: "竞品数据", 2: "定价$12"}, llm)
        assert result["score"] == 100
        assert result["passed"] is True

    def test_partial_pass_score_33(self):
        llm = make_llm(_PARTIAL_PASS)
        result = Validator.check(_PLAN, {1: "竞品数据", 2: "无数字"}, llm)
        assert result["score"] == 33
        assert result["passed"] is False
        assert result["failed_tasks"] == [2]

    def test_invalid_json_marks_all_failed(self):
        llm = make_llm("不是JSON")
        result = Validator.check(_PLAN, {1: "ok"}, llm)
        assert result["passed"] is False
        assert len(result["checklist"]) == 3
        assert all(not c["passed"] for c in result["checklist"])

    def test_score_overrides_llm_score(self):
        """score 字段由 compute_score 硬编码计算，不依赖 LLM。"""
        resp = json.dumps({
            "checklist": [{"criterion": "c", "passed": True, "evidence": "e"}],
            "failed_tasks": [],
            "suggestion": "",
        })
        llm = make_llm(resp)
        result = Validator.check(_PLAN, {}, llm)
        assert result["score"] == 100  # 1/1 passed
