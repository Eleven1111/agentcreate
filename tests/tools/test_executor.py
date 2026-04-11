from tools.smart_router.executor import Executor

_PLAN = {
    "true_intent": "分析竞品并给出定价建议",
    "tasks": [
        {"id": 1, "name": "竞品收集", "instruction": "列出竞品"},
        {"id": 2, "name": "定价建议", "instruction": "给出定价区间"},
    ],
}


def make_llm(response: str):
    def llm(messages):
        return response
    return llm


class TestExecutorRunTask:
    def test_returns_llm_output(self):
        llm = make_llm("竞品A：$10，竞品B：$15")
        result = Executor.run_task(_PLAN["tasks"][0], _PLAN, {}, llm)
        assert result == "竞品A：$10，竞品B：$15"

    def test_previous_results_injected_into_prompt(self):
        captured = []
        def llm(messages):
            captured.append(messages[0]["content"])
            return "定价建议：$12"
        Executor.run_task(
            _PLAN["tasks"][1],
            _PLAN,
            {1: "竞品A：$10，竞品B：$15"},
            llm,
        )
        assert "竞品A：$10，竞品B：$15" in captured[0]

    def test_failed_previous_results_excluded(self):
        """FAILED 结果不应注入到下一个任务的上下文中。"""
        captured = []
        def llm(messages):
            captured.append(messages[0]["content"])
            return "ok"
        Executor.run_task(
            _PLAN["tasks"][1],
            _PLAN,
            {1: "FAILED"},
            llm,
        )
        assert "FAILED" not in captured[0]

    def test_llm_exception_returns_failed(self):
        def llm(messages):
            raise RuntimeError("网络错误")
        result = Executor.run_task(_PLAN["tasks"][0], _PLAN, {}, llm)
        assert result == "FAILED"

    def test_true_intent_in_prompt(self):
        captured = []
        def llm(messages):
            captured.append(messages[0]["content"])
            return "ok"
        Executor.run_task(_PLAN["tasks"][0], _PLAN, {}, llm)
        assert "分析竞品并给出定价建议" in captured[0]
