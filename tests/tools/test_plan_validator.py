"""tests/tools/test_plan_validator.py — plan_validator 单元测试"""
import pytest
from dataclasses import dataclass, field
from tools.execute_plan.plan_validator import (
    validate_structure,
    validate_traceability,
    _find_cycles,
    _parse_traceability,
    _safe_all_covered,
)


@dataclass
class FakeTask:
    id: int
    name: str
    depends_on: list = field(default_factory=list)


# ── validate_structure ────────────────────────────────────────────────────────

class TestValidateStructure:
    def _tasks(self, specs):
        return [FakeTask(id=i+1, name=f"Task {i+1}", depends_on=d)
                for i, d in enumerate(specs)]

    def test_valid_plan_returns_no_issues(self):
        tasks = self._tasks([[], [1], [1, 2]])
        assert validate_structure(tasks) == []

    def test_single_task_flagged(self):
        tasks = [FakeTask(id=1, name="only task", depends_on=[])]
        issues = validate_structure(tasks)
        assert any("太少" in i for i in issues)

    def test_too_many_tasks_flagged(self):
        tasks = [FakeTask(id=i+1, name=f"t{i}", depends_on=[]) for i in range(26)]
        issues = validate_structure(tasks)
        assert any("过多" in i for i in issues)

    def test_non_sequential_ids_flagged(self):
        tasks = [FakeTask(id=1, name="a"), FakeTask(id=3, name="b")]
        issues = validate_structure(tasks)
        assert any("连续" in i for i in issues)

    def test_cycle_detected(self):
        # 1 → 2 → 3 → 1
        tasks = [
            FakeTask(id=1, name="a", depends_on=[3]),
            FakeTask(id=2, name="b", depends_on=[1]),
            FakeTask(id=3, name="c", depends_on=[2]),
        ]
        issues = validate_structure(tasks)
        assert any("循环" in i for i in issues)

    def test_no_cycle_linear_chain(self):
        tasks = [
            FakeTask(id=1, name="a", depends_on=[]),
            FakeTask(id=2, name="b", depends_on=[1]),
            FakeTask(id=3, name="c", depends_on=[2]),
        ]
        assert validate_structure(tasks) == []

    def test_diamond_dependency_not_cycle(self):
        # 1 → 2, 1 → 3, 2 → 4, 3 → 4
        tasks = [
            FakeTask(id=1, name="a", depends_on=[]),
            FakeTask(id=2, name="b", depends_on=[1]),
            FakeTask(id=3, name="c", depends_on=[1]),
            FakeTask(id=4, name="d", depends_on=[2, 3]),
        ]
        assert validate_structure(tasks) == []

    def test_exactly_two_tasks_is_valid(self):
        tasks = self._tasks([[], [1]])
        assert validate_structure(tasks) == []

    def test_exactly_25_tasks_is_valid(self):
        tasks = [FakeTask(id=i+1, name=f"t{i}", depends_on=[]) for i in range(25)]
        assert validate_structure(tasks) == []


# ── _find_cycles ──────────────────────────────────────────────────────────────

class TestFindCycles:
    def test_no_cycle_returns_empty(self):
        tasks = [FakeTask(id=1, name="a"), FakeTask(id=2, name="b", depends_on=[1])]
        assert _find_cycles(tasks) == []

    def test_self_loop_detected(self):
        tasks = [FakeTask(id=1, name="a", depends_on=[1])]
        assert 1 in _find_cycles(tasks)

    def test_two_node_cycle(self):
        tasks = [
            FakeTask(id=1, name="a", depends_on=[2]),
            FakeTask(id=2, name="b", depends_on=[1]),
        ]
        cycles = _find_cycles(tasks)
        assert 1 in cycles and 2 in cycles


# ── validate_traceability ─────────────────────────────────────────────────────

class TestValidateTraceability:
    def _make_llm(self, response: str):
        def llm(messages):
            return response
        return llm

    def _tasks(self):
        return [
            FakeTask(id=1, name="用户登录"),
            FakeTask(id=2, name="数据展示"),
            FakeTask(id=3, name="Redis 缓存层"),  # 用户未要求
        ]

    def test_all_covered(self):
        llm = self._make_llm(
            '{"covered": [1, 2, 3], "uncovered": [], "reasons": {}}'
        )
        result = validate_traceability(self._tasks(), "需要用户登录和数据展示", llm)
        assert result["uncovered"] == []
        assert set(result["covered"]) == {1, 2, 3}

    def test_uncovered_task_identified(self):
        llm = self._make_llm(
            '{"covered": [1, 2], "uncovered": [3], "reasons": {"3": "用户未要求 Redis"}}'
        )
        result = validate_traceability(self._tasks(), "需要用户登录和数据展示", llm)
        assert 3 in result["uncovered"]
        assert "3" in result["reasons"]

    def test_invalid_json_returns_all_covered(self):
        llm = self._make_llm("这不是 JSON，LLM 跑偏了")
        result = validate_traceability(self._tasks(), "需要求", llm)
        assert result["uncovered"] == []
        assert set(result["covered"]) == {1, 2, 3}

    def test_llm_exception_returns_all_covered(self):
        def bad_llm(messages):
            raise RuntimeError("网络错误")
        result = validate_traceability(self._tasks(), "需要求", bad_llm)
        assert result["uncovered"] == []

    def test_partial_json_in_prose_parsed(self):
        response = '分析如下：\n{"covered": [1], "uncovered": [2, 3], "reasons": {"2": "x", "3": "y"}}\n以上。'
        llm = self._make_llm(response)
        result = validate_traceability(self._tasks(), "需要登录", llm)
        assert 2 in result["uncovered"]
        assert 3 in result["uncovered"]

    def test_missing_ids_default_to_covered(self):
        # LLM 只分类了 1 和 2，漏了 3
        llm = self._make_llm('{"covered": [1], "uncovered": [2], "reasons": {}}')
        result = validate_traceability(self._tasks(), "需要求", llm)
        assert 3 in result["covered"]  # 漏分类的补入 covered

    def test_prompt_contains_task_names(self):
        captured = []
        def llm(messages):
            captured.append(messages[0]["content"])
            return '{"covered": [1,2,3], "uncovered": [], "reasons": {}}'
        validate_traceability(self._tasks(), "需要登录", llm)
        assert "用户登录" in captured[0]
        assert "数据展示" in captured[0]

    def test_requirements_injected_into_prompt(self):
        captured = []
        def llm(messages):
            captured.append(messages[0]["content"])
            return '{"covered": [1,2,3], "uncovered": [], "reasons": {}}'
        validate_traceability(self._tasks(), "唯一需求标识符XYZ", llm)
        assert "唯一需求标识符XYZ" in captured[0]


# ── _safe_all_covered ─────────────────────────────────────────────────────────

class TestSafeAllCovered:
    def test_returns_all_ids_as_covered(self):
        result = _safe_all_covered([1, 2, 3])
        assert result["covered"] == [1, 2, 3]
        assert result["uncovered"] == []
        assert result["reasons"] == {}
