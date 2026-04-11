import pytest
from tools.smart_router.gate import Gate


def make_llm(response: str):
    """返回一个固定响应的 mock llm callable。"""
    def llm(messages):
        return response
    return llm


class TestGateClassify:
    def test_simple_returns_simple(self):
        llm = make_llm('{"complexity": "simple", "reason": "单步问候"}')
        assert Gate.classify("你好", llm) == "simple"

    def test_complex_returns_complex(self):
        llm = make_llm('{"complexity": "complex", "reason": "需要多步推理"}')
        assert Gate.classify("帮我分析竞品策略并给出定价建议", llm) == "complex"

    def test_invalid_json_returns_complex(self):
        llm = make_llm("这不是 JSON")
        assert Gate.classify("任意消息", llm) == "complex"

    def test_missing_complexity_key_returns_complex(self):
        llm = make_llm('{"reason": "没有 complexity 字段"}')
        assert Gate.classify("任意消息", llm) == "complex"

    def test_prompt_contains_message(self):
        """确认 message 被注入进 prompt。"""
        captured = []
        def llm(messages):
            captured.append(messages[0]["content"])
            return '{"complexity": "simple", "reason": "ok"}'
        Gate.classify("特定消息内容XYZ", llm)
        assert "特定消息内容XYZ" in captured[0]
