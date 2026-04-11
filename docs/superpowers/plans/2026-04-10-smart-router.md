# Smart Router Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a pre-intercept routing layer to OpenClaw that detects request complexity, performs intent analysis + task decomposition on complex requests, runs sequential subagent execution, then validates alignment before delivery.

**Architecture:** A `handle_message` entrypoint in `skills/smart_router/smart_router.py` calls four tools in sequence: Gate (complexity gate) → Planner (intent + decomposition) → Executor (sequential task runner) → Validator (checklist + score + retry). State is persisted per-user in `~/.openclaw/smart-router/{user_id}.json`. Simple requests bypass the pipeline entirely.

**Tech Stack:** Python 3.12, stdlib only (json, pathlib). No external dependencies. Tests use pytest with inline mock ctx.

---

## File Map

| File | Role |
|------|------|
| `tools/smart_router/__init__.py` | Package marker |
| `tools/smart_router/gate.py` | `Gate.classify(message, llm) → "simple"\|"complex"` |
| `tools/smart_router/planner.py` | `Planner.extract(message, llm) → IntentPlan` |
| `tools/smart_router/executor.py` | `Executor.run_task(task, plan, results, llm) → str` |
| `tools/smart_router/validator.py` | `Validator.check(plan, results, llm) → ValidationResult`, `compute_score(checklist) → int` |
| `skills/smart_router/smart_router.py` | `handle_message(text, user_id, ctx)` — main entrypoint + state machine |
| `skills/smart_router/smart-router.md` | OpenClaw skill descriptor |
| `tests/__init__.py` | Package marker |
| `tests/tools/__init__.py` | Package marker |
| `tests/tools/test_gate.py` | Gate unit tests |
| `tests/tools/test_planner.py` | Planner unit tests |
| `tests/tools/test_executor.py` | Executor unit tests |
| `tests/tools/test_validator.py` | Validator unit tests |
| `tests/skills/__init__.py` | Package marker |
| `tests/skills/test_smart_router.py` | Integration tests for handle_message |

---

## Task 1: Project scaffolding + Gate

**Files:**
- Create: `tools/smart_router/__init__.py`
- Create: `tools/smart_router/gate.py`
- Create: `tests/__init__.py`
- Create: `tests/tools/__init__.py`
- Create: `tests/tools/test_gate.py`

- [ ] **Step 1: Create package markers**

```bash
mkdir -p tools/smart_router tests/tools tests/skills
touch tools/smart_router/__init__.py tests/__init__.py tests/tools/__init__.py tests/skills/__init__.py
```

- [ ] **Step 2: Write the failing test**

Create `tests/tools/test_gate.py`:

```python
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
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd /Users/na/na/Claudecode/agentcreate
python -m pytest tests/tools/test_gate.py -v
```

Expected: `ModuleNotFoundError: No module named 'tools.smart_router.gate'`

- [ ] **Step 4: Implement gate.py**

Create `tools/smart_router/gate.py`:

```python
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
        except (json.JSONDecodeError, AttributeError):
            return "complex"
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/tools/test_gate.py -v
```

Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add tools/smart_router/__init__.py tools/smart_router/gate.py \
        tests/__init__.py tests/tools/__init__.py tests/skills/__init__.py \
        tests/tools/test_gate.py
git commit -m "feat: add Gate complexity classifier for smart-router"
```

---

## Task 2: Planner

**Files:**
- Create: `tools/smart_router/planner.py`
- Create: `tests/tools/test_planner.py`

- [ ] **Step 1: Write the failing test**

Create `tests/tools/test_planner.py`:

```python
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

    def test_prompt_contains_message(self):
        captured = []
        def llm(messages):
            captured.append(messages[0]["content"])
            return json.dumps(_VALID_PLAN)
        Planner.extract("特定消息内容ABC", llm)
        assert "特定消息内容ABC" in captured[0]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/tools/test_planner.py -v
```

Expected: `ModuleNotFoundError: No module named 'tools.smart_router.planner'`

- [ ] **Step 3: Implement planner.py**

Create `tools/smart_router/planner.py`:

```python
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
        except json.JSONDecodeError as e:
            raise ValueError(f"Planner LLM 返回无效 JSON: {e}\nRaw: {raw[:200]}")
        plan.setdefault("raw_request", message)
        return plan
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/tools/test_planner.py -v
```

Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add tools/smart_router/planner.py tests/tools/test_planner.py
git commit -m "feat: add Planner intent extractor for smart-router"
```

---

## Task 3: Executor

**Files:**
- Create: `tools/smart_router/executor.py`
- Create: `tests/tools/test_executor.py`

- [ ] **Step 1: Write the failing test**

Create `tests/tools/test_executor.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/tools/test_executor.py -v
```

Expected: `ModuleNotFoundError: No module named 'tools.smart_router.executor'`

- [ ] **Step 3: Implement executor.py**

Create `tools/smart_router/executor.py`:

```python
"""executor.py — 顺序子任务执行器"""

_TASK_PROMPT = """你是一个专注执行单一子任务的助手。

整体目标：{true_intent}

已完成的子任务结果：
{previous_results}

当前子任务：{task_name}
执行指令：{instruction}

直接输出执行结果，不要重复目标描述。"""


class Executor:
    @staticmethod
    def run_task(task: dict, plan: dict, results: dict, llm) -> str:
        """执行单个子任务，返回输出字符串。LLM 异常时返回 "FAILED"。"""
        prev = "\n".join(
            f"[子任务 {tid}] {output}"
            for tid, output in results.items()
            if output != "FAILED"
        ) or "（无）"

        prompt = _TASK_PROMPT.format(
            true_intent=plan["true_intent"],
            previous_results=prev,
            task_name=task["name"],
            instruction=task["instruction"],
        )
        try:
            return llm([{"role": "user", "content": prompt}])
        except Exception:
            return "FAILED"
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/tools/test_executor.py -v
```

Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add tools/smart_router/executor.py tests/tools/test_executor.py
git commit -m "feat: add Executor sequential task runner for smart-router"
```

---

## Task 4: Validator

**Files:**
- Create: `tools/smart_router/validator.py`
- Create: `tests/tools/test_validator.py`

- [ ] **Step 1: Write the failing test**

Create `tests/tools/test_validator.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/tools/test_validator.py -v
```

Expected: `ModuleNotFoundError: No module named 'tools.smart_router.validator'`

- [ ] **Step 3: Implement validator.py**

Create `tools/smart_router/validator.py`:

```python
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
        except json.JSONDecodeError:
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/tools/test_validator.py -v
```

Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add tools/smart_router/validator.py tests/tools/test_validator.py
git commit -m "feat: add Validator checklist scorer for smart-router"
```

---

## Task 5: Main entrypoint smart_router.py

**Files:**
- Create: `skills/smart_router/smart_router.py`
- Create: `tests/skills/test_smart_router.py`

- [ ] **Step 1: Create skills directory**

```bash
mkdir -p skills/smart_router
```

- [ ] **Step 2: Write the failing integration tests**

Create `tests/skills/test_smart_router.py`:

```python
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
```

- [ ] **Step 3: Run test to verify it fails**

```bash
python -m pytest tests/skills/test_smart_router.py -v
```

Expected: `ModuleNotFoundError: No module named 'skills.smart_router'`

- [ ] **Step 4: Implement smart_router.py**

Create `skills/smart_router/smart_router.py`:

```python
"""smart_router.py — OpenClaw smart-router skill 主入口

状态机：
  idle → executing → validating → done
           ↑              ↓ (score < 80，retries < MAX_RETRIES)
           └──────────────┘ (重跑 failed_tasks)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tools.smart_router.gate import Gate
from tools.smart_router.planner import Planner
from tools.smart_router.executor import Executor
from tools.smart_router.validator import Validator

MAX_RETRIES = 2
_DIALOG_DIR = Path.home() / ".openclaw" / "smart-router"


# ── 状态持久化 ────────────────────────────────────────────────────────────────

def _load(user_id: str) -> dict:
    _DIALOG_DIR.mkdir(parents=True, exist_ok=True)
    p = _DIALOG_DIR / f"{user_id}.json"
    return json.loads(p.read_text()) if p.exists() else {"phase": "idle"}


def _save(user_id: str, state: dict) -> None:
    _DIALOG_DIR.mkdir(parents=True, exist_ok=True)
    (_DIALOG_DIR / f"{user_id}.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2)
    )


def _reset(user_id: str) -> None:
    p = _DIALOG_DIR / f"{user_id}.json"
    if p.exists():
        p.unlink()


# ── 主入口 ───────────────────────────────────────────────────────────────────

def handle_message(text: str, user_id: str, ctx) -> None:
    """
    OpenClaw entrypoint。
    ctx 由框架注入，提供：
      ctx.reply(text)      — 通过当前渠道回复用户
      ctx.llm(messages)    — 调用框架已配置的 LLM，返回 str
    """
    if "重置路由" in text:
        _reset(user_id)
        ctx.reply("✅ 已重置")
        return

    state = _load(user_id)
    try:
        phase = state.get("phase", "idle")
        if phase == "idle":
            _on_idle(text, user_id, state, ctx)
        elif phase == "executing":
            _run_executor(user_id, state, ctx)
        elif phase == "validating":
            _run_validator(user_id, state, ctx)
    except Exception as e:
        ctx.reply(f"⚠️ 出错了：{e}\n输入「重置路由」清除状态后重试。")
        raise


# ── 阶段处理 ─────────────────────────────────────────────────────────────────

def _on_idle(text: str, user_id: str, state: dict, ctx) -> None:
    complexity = Gate.classify(text, ctx.llm)
    if complexity == "simple":
        answer = ctx.llm([{"role": "user", "content": text}])
        ctx.reply(answer)
        return

    ctx.reply("🔍 分析你的请求...")
    plan = Planner.extract(text, ctx.llm)
    state.update({
        "phase": "executing",
        "plan": plan,
        "retries": 0,
        "results": {},
        "retry_tasks": [t["id"] for t in plan["tasks"]],
        "suggestion": "",
    })
    _save(user_id, state)
    _run_executor(user_id, state, ctx)


def _run_executor(user_id: str, state: dict, ctx) -> None:
    plan = state["plan"]
    results = state.get("results", {})
    retry_tasks = state.get("retry_tasks", [t["id"] for t in plan["tasks"]])

    ctx.reply(f"⚙️ 执行 {len(retry_tasks)} 个子任务...")
    for task in plan["tasks"]:
        if task["id"] not in retry_tasks:
            continue
        t = dict(task)
        if state["retries"] > 0 and state.get("suggestion"):
            t["instruction"] = f"{t['instruction']}\n\n改进要求：{state['suggestion']}"
        output = Executor.run_task(t, plan, results, ctx.llm)
        results[task["id"]] = output
        ctx.reply(f"  ✓ {task['name']}")

    state.update({"phase": "validating", "results": results})
    _save(user_id, state)
    _run_validator(user_id, state, ctx)


def _run_validator(user_id: str, state: dict, ctx) -> None:
    plan = state["plan"]
    results = state["results"]
    validation = Validator.check(plan, results, ctx.llm)

    if validation["passed"]:
        ctx.reply(f"✅ 验收通过（{validation['score']}分）\n\n{_merge_results(results, plan)}")
        state["phase"] = "done"
    elif state["retries"] >= MAX_RETRIES:
        ctx.reply(
            f"⚠️ 已重试 {MAX_RETRIES} 次，当前得分 {validation['score']}，直接交付：\n\n"
            f"{_merge_results(results, plan)}"
        )
        state["phase"] = "done"
    else:
        state["retries"] += 1
        state["retry_tasks"] = validation["failed_tasks"]
        state["suggestion"] = validation.get("suggestion", "")
        state["phase"] = "executing"
        ctx.reply(f"🔄 得分 {validation['score']}，重试第 {state['retries']} 次...")
        _save(user_id, state)
        _run_executor(user_id, state, ctx)

    _save(user_id, state)


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _merge_results(results: dict, plan: dict) -> str:
    """按 task 顺序拼接各子任务输出，用标题分隔。FAILED 结果跳过。"""
    parts = []
    for task in plan["tasks"]:
        output = results.get(task["id"], "（未执行）")
        if output != "FAILED":
            parts.append(f"**{task['name']}**\n\n{output}")
    return "\n\n---\n\n".join(parts)
```

- [ ] **Step 5: Add `skills/__init__.py` and `skills/smart_router/__init__.py`**

```bash
touch skills/__init__.py skills/smart_router/__init__.py
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
python -m pytest tests/skills/test_smart_router.py -v
```

Expected: 5 passed

- [ ] **Step 7: Run full test suite**

```bash
python -m pytest tests/ -v
```

Expected: all previously passing tests still pass

- [ ] **Step 8: Commit**

```bash
git add skills/__init__.py skills/smart_router/__init__.py \
        skills/smart_router/smart_router.py \
        tests/skills/test_smart_router.py
git commit -m "feat: add smart_router main entrypoint with state machine"
```

---

## Task 6: Skill descriptor

**Files:**
- Create: `skills/smart_router/smart-router.md`

- [ ] **Step 1: Create skill descriptor**

Create `skills/smart_router/smart-router.md`:

```markdown
---
name: smart-router
description: OpenClaw 智能路由层。所有消息的前置拦截器——自动判断复杂度，复杂请求做意图分析+任务拆解+多步执行+对齐验收，简单请求直接透传。输入「重置路由」可清除当前用户状态。
triggers:
  - type: message
    priority: lowest
    match: "*"
entrypoint: skills/smart_router/smart_router.py::handle_message
---

## 功能说明

`smart-router` 是 OpenClaw 的前置拦截层，触发优先级最低（`priority: lowest`），确保精确 skill 先匹配，兜底才走此路由。

### 处理流程

**简单请求**（问候、单一事实查询）：
```
消息 → Gate 判断 simple → 直接 LLM 回答 → 返回
```

**复杂请求**（多步推理、意图模糊）：
```
消息 → Gate 判断 complex
     → Planner 提取真实意图 + 拆解 2-5 个子任务 + 生成验收清单
     → Executor 顺序执行各子任务（前序结果滚动传入）
     → Validator 清单核查 + 评分（满分100）
          ≥ 80分 → 交付结果
          < 80分 → 重跑失败子任务（最多重试 2 次）→ 交付
```

### 状态持久化

对话状态存储在 `~/.openclaw/smart-router/{user_id}.json`，支持断点续跑。

### 重置

发送「重置路由」清除当前用户的对话状态。
```

- [ ] **Step 2: Verify descriptor structure**

```bash
head -10 skills/smart_router/smart-router.md
```

Expected output starts with `---` and contains `name: smart-router`

- [ ] **Step 3: Run full test suite one final time**

```bash
python -m pytest tests/ -v --tb=short
```

Expected: all tests pass

- [ ] **Step 4: Final commit**

```bash
git add skills/smart_router/smart-router.md
git commit -m "feat: add smart-router skill descriptor — OpenClaw intelligent routing layer complete"
```
