import json
import pytest
from pathlib import Path


class MockCtx:
    def __init__(self, llm_responses: list[str]):
        self.replies: list[str] = []
        self._responses = iter(llm_responses)

    def reply(self, text: str) -> None:
        self.replies.append(text)

    def llm(self, messages: list[dict]) -> str:
        try:
            return next(self._responses)
        except StopIteration:
            return '{"error": "no more responses"}'


_SIMPLE_GATE = '{"complexity": "simple", "reason": "单步问候"}'
_COMPLEX_GATE = '{"complexity": "complex", "reason": "需要多步"}'
_PLAN = json.dumps({
    "raw_request": "帮我分析竞品",
    "true_intent": "了解竞品以定价",
    "acceptance_criteria": ["包含竞品对比"],
    "tasks": [{"id": 1, "name": "竞品分析", "instruction": "分析竞品"}],
})
_PASS_VALIDATION = json.dumps({
    "checklist": [{"criterion": "包含竞品对比", "passed": True, "evidence": "有"}],
    "failed_tasks": [],
    "suggestion": "",
})
_FAIL_VALIDATION = json.dumps({
    "checklist": [{"criterion": "包含竞品对比", "passed": False, "evidence": "无"}],
    "failed_tasks": [1],
    "suggestion": "需要补充竞品数据",
})


def get_handle(tmp_path, monkeypatch):
    """在临时目录运行 smart_router，避免污染真实 ~/.openclaw。"""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))

    import importlib
    import skills.smart_router.smart_router as sr_module
    monkeypatch.setattr(sr_module, "_DIALOG_DIR", tmp_path / "smart-router")
    return sr_module.handle_message


class TestSimplePath:
    def test_simple_request_direct_reply(self, tmp_path, monkeypatch):
        handle = get_handle(tmp_path, monkeypatch)
        ctx = MockCtx([_SIMPLE_GATE, "你好！有什么我可以帮你的？"])
        handle("你好", "user1", ctx)
        assert len(ctx.replies) == 1
        assert ctx.replies[0] == "你好！有什么我可以帮你的？"

    def test_reset_clears_state(self, tmp_path, monkeypatch):
        handle = get_handle(tmp_path, monkeypatch)
        ctx = MockCtx([])
        handle("重置路由", "user1", ctx)
        assert ctx.replies[0] == "✅ 已重置"


class TestComplexPath:
    def test_complex_request_goes_through_pipeline(self, tmp_path, monkeypatch):
        handle = get_handle(tmp_path, monkeypatch)
        ctx = MockCtx([
            _COMPLEX_GATE,           # Gate → complex
            _PLAN,                   # Planner → IntentPlan
            "竞品A $10, 竞品B $15",  # Executor task 1
            _PASS_VALIDATION,        # Validator → passed
        ])
        handle("帮我分析竞品", "user1", ctx)
        # 最后一条回复应包含验收通过
        assert any("验收通过" in r for r in ctx.replies)
        assert any("100" in r for r in ctx.replies)

    def test_retry_on_low_score(self, tmp_path, monkeypatch):
        handle = get_handle(tmp_path, monkeypatch)
        ctx = MockCtx([
            _COMPLEX_GATE,            # Gate
            _PLAN,                    # Planner
            "竞品数据不完整",          # Executor task 1（第1次）
            _FAIL_VALIDATION,         # Validator → failed, score=0
            "更完整的竞品数据",        # Executor task 1（重试）
            _PASS_VALIDATION,         # Validator → passed
        ])
        handle("帮我分析竞品", "user2", ctx)
        assert any("重试" in r for r in ctx.replies)
        assert any("验收通过" in r for r in ctx.replies)

    def test_max_retries_delivers_anyway(self, tmp_path, monkeypatch):
        handle = get_handle(tmp_path, monkeypatch)
        ctx = MockCtx([
            _COMPLEX_GATE,
            _PLAN,
            "差结果1",   _FAIL_VALIDATION,   # 第1次执行+验收
            "差结果2",   _FAIL_VALIDATION,   # 重试1
            "差结果3",   _FAIL_VALIDATION,   # 重试2（达到上限）
        ])
        handle("帮我分析竞品", "user3", ctx)
        assert any("已重试" in r for r in ctx.replies)
