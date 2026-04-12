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
