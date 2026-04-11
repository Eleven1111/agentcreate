"""gate.py — 请求复杂度二分类器"""
import json

_GATE_PROMPT = """判断以下用户消息的处理复杂度。

用户消息：
{message}

规则：
- simple：单步可答，无需拆解（问候、查询单一事实、直接执行指令）
- complex：需要多步推理、跨领域协调、或意图不明确需要澄清

只输出 JSON，不要解释：
{{"complexity": "simple" | "complex", "reason": "一句话原因"}}"""


class Gate:
    @staticmethod
    def classify(message: str, llm) -> str:
        """返回 "simple" 或 "complex"。解析失败时保守返回 "complex"。"""
        prompt = _GATE_PROMPT.format(message=message)
        raw = llm([{"role": "user", "content": prompt}])
        try:
            data = json.loads(raw)
            return data.get("complexity", "complex")
        except (json.JSONDecodeError, AttributeError, TypeError):
            return "complex"
