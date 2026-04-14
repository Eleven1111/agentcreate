"""execute_plan.py — OpenClaw execute-plan skill 主 entrypoint

设计原则：
  - 消息发送：ctx.reply(text) — 平台无关，框架负责路由到企微/TG/Discord/飞书
  - LLM 调用：ctx.llm(messages) — 框架负责连接已配置的模型
  - Skill 只写业务逻辑，不感知平台，不管 LLM 厂商

对话状态机：
  idle → phase0_q → phase0_confirm → phase1_plan
       → [phase1_review] → phase2_exec → [blocked] → phase3_validate → done

phase1_review：计划验证发现疑似瞎编任务时，暂停让用户决策（删除/保留）。
"""
import re
import subprocess
from datetime import date
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from tools.execute_plan.plan_parser import PlanParser
from tools.execute_plan import state_manager as sm
from tools.execute_plan.wave_executor import WaveExecutor
from tools.execute_plan.plan_validator import validate_structure, validate_traceability

_QUESTIONS = [
    "👋 我来帮你完成这个项目。先了解背景：\n\n**终端用户是谁？** 技术水平如何？日常工作流程是什么？",
    "**核心痛点是什么？** 现在哪些步骤最费时、最容易出错？",
    "**有哪些硬约束？** 入口平台、已有技术栈、部署限制、预算等。",
    "**交付后谁来维护？** 用户自己维护、你来维护、还是完全自动运行不需要人工介入？",
]


def handle_message(text: str, user_id: str, ctx) -> None:
    """
    OpenClaw entrypoint。
    ctx 由框架注入，提供：
      ctx.reply(text)         — 通过当前渠道回复用户
      ctx.llm(messages)       — 调用框架已配置的 LLM，返回 str
    """
    if any(kw in text for kw in ["重置开发", "reset execute-plan"]):
        sm.reset_dialog(user_id)
        ctx.reply("✅ 状态已重置，发送「开始开发」或「执行计划 path/to/plan.md」重新开始。")
        return

    state = sm.load_dialog(user_id)
    try:
        dispatch = {
            "idle":            _on_idle,
            "phase0_q":        _on_phase0_q,
            "phase0_confirm":  _on_phase0_confirm,
            "phase1_plan":     _on_phase1_plan,
            "phase1_review":   _on_phase1_review,
            "phase2_exec":     _on_phase2_exec,
            "blocked":         _on_blocked,
            "phase3_validate": _on_phase3_validate,
        }
        dispatch.get(state.get("phase", "idle"), _on_idle)(text, user_id, state, ctx)
        sm.save_dialog(user_id, state)
    except Exception as e:
        ctx.reply(f"⚠️ 出错了：{e}\n输入「重置开发」清除状态后重试。")
        raise


# ── 阶段处理 ─────────────────────────────────────────────────────────────────

def _on_idle(text, user_id, state, ctx):
    plan_path = _extract_plan_path(text)
    if plan_path and Path(plan_path).exists():
        state.update({"plan_path": plan_path, "phase": "phase2_exec"})
        ctx.reply(f"📋 找到计划：{plan_path}\n开始执行...")
        _run_phase2(user_id, state, ctx)
    else:
        state.update({"phase": "phase0_q", "answers": [], "qa_round": 0})
        ctx.reply(_QUESTIONS[0])


def _on_phase0_q(text, user_id, state, ctx):
    state["answers"].append(text)
    if len(state["answers"]) < len(_QUESTIONS):
        ctx.reply(_QUESTIONS[len(state["answers"])])
    else:
        _generate_proposals(user_id, state, ctx)


def _on_phase0_confirm(text, user_id, state, ctx):
    if any(kw in text.lower() for kw in ["确认", "好", "可以", "ok", "yes", "继续", "就这个", "没问题"]):
        ctx.reply("✅ 方案确认！正在生成实现计划...")
        state["phase"] = "phase1_plan"
        _run_phase1(user_id, state, ctx)
    else:
        state["answers"].append(f"调整意见：{text}")
        _generate_proposals(user_id, state, ctx)


def _on_phase1_plan(text, user_id, state, ctx):
    ctx.reply("计划生成中，请稍候...")


def _on_phase1_review(text, user_id, state, ctx):
    """用户对疑似瞎编任务的回应：删除 / 保留 / 其他意见。"""
    plan_path = state["plan_path"]
    uncovered = state.get("uncovered_tasks", [])

    if any(kw in text for kw in ["删除", "remove", "不要", "去掉"]):
        _prune_tasks(plan_path, uncovered)
        ctx.reply(f"✅ 已删除 {len(uncovered)} 个无需求来源的任务，继续执行...")
        state["phase"] = "phase2_exec"
        _run_phase2(user_id, state, ctx)

    elif any(kw in text for kw in ["保留", "keep", "没问题", "ok", "可以", "继续"]):
        ctx.reply("✅ 保留所有任务，继续执行...")
        state["phase"] = "phase2_exec"
        _run_phase2(user_id, state, ctx)

    else:
        # 用户给出具体意见，追加到 answers 后重新生成
        state["answers"].append(f"计划修订意见：{text}")
        ctx.reply("收到修订意见，重新生成计划...")
        state["phase"] = "phase1_plan"
        _run_phase1(user_id, state, ctx)


def _on_phase2_exec(text, user_id, state, ctx):
    _run_phase2(user_id, state, ctx)


def _on_blocked(text, user_id, state, ctx):
    plan_path = state["plan_path"]
    exec_state = sm.load_exec(plan_path, [])
    blocked_id = state.get("blocked_task_id")

    if "跳过" in text:
        if blocked_id:
            _move(exec_state, blocked_id, "in_progress", "failed")
        state.pop("blocked_task_id", None)
        state["phase"] = "phase2_exec"
        sm.save_exec(plan_path, exec_state)
        ctx.reply("已跳过，继续执行剩余任务...")
        _run_phase2(user_id, state, ctx)

    elif "重试" in text:
        if blocked_id:
            _move(exec_state, blocked_id, "in_progress", "pending")
        state.pop("blocked_task_id", None)
        state["phase"] = "phase2_exec"
        sm.save_exec(plan_path, exec_state)
        ctx.reply("重新加入队列，继续执行...")
        _run_phase2(user_id, state, ctx)

    else:
        state.setdefault("fix_hints", []).append(text)
        state["phase"] = "phase2_exec"
        ctx.reply("收到，正在处理你的指示...")
        _run_phase2(user_id, state, ctx)


def _on_phase3_validate(text, user_id, state, ctx):
    _run_phase3(user_id, state, ctx)


# ── 核心执行逻辑 ──────────────────────────────────────────────────────────────

def _generate_proposals(user_id, state, ctx):
    answers = state.get("answers", [])
    qa_pairs = "\n".join(
        f"- {q.split(chr(10))[0].strip('*# ')}：{a}"
        for q, a in zip(_QUESTIONS, answers[:4])
    )
    extra = "\n".join(f"- 补充：{a}" for a in answers[4:])
    proposal = ctx.llm([{"role": "user", "content": f"""用户要做一个软件项目，背景：
{qa_pairs}
{extra}

请提出 3 个候选方案，每个包含：核心定位（一句话）、数据层、触发入口、维护复杂度（低/中/高）、失败后果（轻/中/重）。
Markdown 表格对比，推荐一个并说明原因。结尾问：「确认这个方案继续？还是需要调整？」"""}])
    state["proposals"] = proposal
    state["phase"] = "phase0_confirm"
    ctx.reply(proposal)


def _run_phase1(user_id, state, ctx):
    answers = state.get("answers", [])
    plan_content = ctx.llm([{"role": "user", "content": f"""基于以下需求生成详细 plan.md。

背景：
{chr(10).join(f"- {a}" for a in answers)}

确认方案：
{state.get("proposals", "")}

Plan 格式要求（严格遵守）：
1. 每个任务 `## Task N: [名称]`
2. 紧跟 `depends_on: []` 或 `depends_on: [N, M]`
3. 无依赖写 `depends_on: []`，可同一波次并行
4. 基础设施任务排前面，被 skill 任务依赖
5. 每个任务含：文件列表、测试代码（先写）、实现代码、commit 命令
6. 最后一个任务是全量验收，depends_on 列出所有其他 ID

只输出 plan.md 内容。"""}])

    plan_path = f"docs/plans/{date.today().isoformat()}-project.md"
    Path("docs/plans").mkdir(parents=True, exist_ok=True)
    Path(plan_path).write_text(plan_content, encoding="utf-8")
    state["plan_path"] = plan_path

    # ── Phase 1.5: 结构验证 ──────────────────────────────────────────────────
    parser = PlanParser(plan_path)
    struct_issues = validate_structure(parser.tasks)
    if struct_issues:
        ctx.reply(
            "⚠️ 生成的计划结构有问题，正在重新生成：\n"
            + "\n".join(f"  · {i}" for i in struct_issues)
        )
        _run_phase1(user_id, state, ctx)
        return

    # ── Phase 1.5: 需求追溯 ──────────────────────────────────────────────────
    requirements_text = (
        "\n".join(answers)
        + "\n\n确认方案：\n"
        + state.get("proposals", "")
    )
    tracing = validate_traceability(parser.tasks, requirements_text, ctx.llm)

    if tracing["uncovered"]:
        uncovered_names = [
            f"Task {tid}: {t.name} — {tracing['reasons'].get(str(tid), '未说明')}"
            for tid in tracing["uncovered"]
            if (t := parser.get_task(tid))
        ]
        ctx.reply(
            f"⚠️ 发现 {len(tracing['uncovered'])} 个任务找不到需求来源（疑似 AI 自行添加）：\n"
            + "\n".join(f"  · {n}" for n in uncovered_names)
            + "\n\n输入「删除」移除这些任务，「保留」全部保留，或描述你的修订意见。"
        )
        state.update({
            "phase": "phase1_review",
            "uncovered_tasks": tracing["uncovered"],
        })
        return

    ctx.reply(
        f"📋 计划验证通过！共 {len(parser.tasks)} 个任务，全部有需求来源。\n"
        f"文件：`{plan_path}`\n开始并行执行..."
    )
    state["phase"] = "phase2_exec"
    _run_phase2(user_id, state, ctx)


def _run_phase2(user_id, state, ctx):
    plan_path = state["plan_path"]
    parser = PlanParser(plan_path)
    all_ids = [t.id for t in parser.tasks]
    exec_state = sm.load_exec(plan_path, all_ids)
    project_context = state.get("proposals", "") or Path(plan_path).read_text()[:500]

    while True:
        completed = set(exec_state["completed"])
        pending = set(exec_state["pending"])
        eligible = parser.get_eligible(completed, pending)

        if not eligible and not pending:
            ctx.reply("🎉 所有任务完成！开始最终验收...")
            state["phase"] = "phase3_validate"
            sm.save_exec(plan_path, exec_state)
            _run_phase3(user_id, state, ctx)
            return

        if not eligible:
            ctx.reply(f"⚠️ 依赖死锁！剩余 pending: {sorted(pending)}\n请检查 plan.md 的 depends_on 是否有循环。")
            state["phase"] = "blocked"
            sm.save_exec(plan_path, exec_state)
            return

        wave_num = exec_state["last_wave"] + 1
        ctx.reply(
            f"🚀 Wave {wave_num} — 并行执行 {len(eligible)} 个任务：\n"
            + "\n".join(f"  · Task {t.id}: {t.name}" for t in eligible)
        )

        for t in eligible:
            exec_state["pending"].remove(t.id)
            exec_state["in_progress"].append(t.id)
        exec_state["last_wave"] = wave_num
        sm.save_exec(plan_path, exec_state)

        # 并行执行（ctx.llm 由框架注入，wave_executor 通过参数接收）
        executor = WaveExecutor(ctx.llm)
        results = executor.run(eligible, plan_path, project_context)

        blocked_task = None
        for task_id, result in results.items():
            _move(exec_state, task_id, "in_progress",
                  "completed" if result["status"] in ("DONE", "DONE_WITH_CONCERNS") else "failed")
            if result["status"] == "DONE_WITH_CONCERNS":
                ctx.reply(f"⚠️ Task {task_id} 完成但有注意：\n{result['concerns']}")
            elif result["status"] == "BLOCKED":
                blocked_task = task_id
                ctx.reply(f"❌ Task {task_id} BLOCKED：{result['detail'][-300:]}\n输入「跳过」「重试」或描述修复方法。")
                break

        sm.save_exec(plan_path, exec_state)

        if blocked_task:
            state.update({"phase": "blocked", "blocked_task_id": blocked_task})
            return

        # Test Gate
        ctx.reply("🧪 Test gate 检查中...")
        gate = _run_gate()
        if gate["passed"]:
            ctx.reply(f"✅ Gate 通过：{gate['summary']}")
        else:
            ctx.reply(f"❌ Gate 失败：\n```\n{gate['output'][:800]}\n```\n正在自动修复...")
            _auto_fix(gate["output"], ctx)
            gate2 = _run_gate()
            if not gate2["passed"]:
                ctx.reply("⚠️ 自动修复失败，需要人工介入。输入修复方法或「跳过」继续。")
                state.update({"phase": "blocked", "gate_output": gate2["output"][:1000]})
                sm.save_exec(plan_path, exec_state)
                return
            ctx.reply("✅ 修复成功，继续执行...")


def _run_phase3(user_id, state, ctx):
    result = subprocess.run(
        ["python", "-m", "pytest", "tests/", "-v", "--tb=short",
         "--cov", "--cov-report=term-missing", "--cov-fail-under=80"],
        capture_output=True, text=True,
    )
    output = result.stdout + result.stderr
    if result.returncode == 0:
        tag = state.get("tag", "v1.0.0")
        subprocess.run(["git", "tag", tag], capture_output=True)
        sm.cleanup_exec(state["plan_path"])
        state["phase"] = "done"
        ctx.reply(f"🎉 验收通过！\n```\n{_tail(output, 8)}\n```\n已打标 `{tag}`")
    else:
        ctx.reply(f"❌ 验收失败：\n```\n{_tail(output, 15)}\n```\n正在修复...")
        _auto_fix(output, ctx)
        state["phase"] = "phase3_validate"


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _prune_tasks(plan_path: str, task_ids: list[int]) -> None:
    """从 plan.md 中物理删除指定任务块，并同步修正其他任务的 depends_on 引用。"""
    text = Path(plan_path).read_text(encoding="utf-8")
    sections = re.split(r"(?=^## Task \d+:)", text, flags=re.MULTILINE)
    remove_set = set(task_ids)
    kept = []
    for section in sections:
        m = re.match(r"^## Task (\d+):", section.strip())
        if m and int(m.group(1)) in remove_set:
            continue
        kept.append(section)

    pruned_text = "".join(kept)

    # 清除 depends_on 中对已删除任务的引用
    def clean_deps(match):
        raw = match.group(1)
        ids = [x.strip() for x in raw.split(",") if x.strip().isdigit()]
        cleaned = [x for x in ids if int(x) not in remove_set]
        return f"depends_on: [{', '.join(cleaned)}]"

    pruned_text = re.sub(r"depends_on:\s*\[([^\]]*)\]", clean_deps, pruned_text)
    Path(plan_path).write_text(pruned_text, encoding="utf-8")

def _run_gate() -> dict:
    r = subprocess.run(["python", "-m", "pytest", "tests/", "-v", "--tb=short"],
                       capture_output=True, text=True)
    output = r.stdout + r.stderr
    summary = next((l for l in reversed(output.split("\n"))
                    if any(w in l for w in ("passed", "failed", "error"))), "")
    return {"passed": r.returncode == 0, "output": output, "summary": summary}


def _auto_fix(test_output: str, ctx) -> None:
    ctx.llm([{"role": "user", "content": f"""以下是 pytest 失败输出，分析根因并直接修复文件。

{test_output}

常见根因：
1. mock 缓存 → lazy import（import module as _mod; _mod.Class()）
2. import 路径错误 → 检查 conftest.py sys.modules 别名
3. 断言不匹配 → 检查实现逻辑

直接修复，完成后说 FIXED 或 NEEDS_HUMAN。"""}])


def _move(exec_state: dict, task_id: int, src: str, dst: str) -> None:
    if task_id in exec_state[src]:
        exec_state[src].remove(task_id)
    if task_id not in exec_state[dst]:
        exec_state[dst].append(task_id)


def _extract_plan_path(text: str) -> str | None:
    m = re.search(r"(docs/plans/[\w\-/.]+\.md)", text)
    return m.group(1) if m else None


def _tail(text: str, n: int) -> str:
    return "\n".join(text.strip().split("\n")[-n:])
