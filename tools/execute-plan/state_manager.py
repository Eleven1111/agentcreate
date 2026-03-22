"""state_manager.py — 两层状态持久化

对话状态 (dialog state)：
  存放位置：~/.openclaw/execute-plan/{user_id}.json
  内容：当前对话阶段、问答记录、plan 文件路径

执行状态 (exec state)：
  存放位置：{plan_path}.state.json（与 plan.md 同目录）
  内容：任务完成情况、当前波次、失败记录
"""
import json
from pathlib import Path


_DIALOG_DIR = Path.home() / ".openclaw" / "execute-plan"


# ── 对话状态 ────────────────────────────────────────────────────────────────

def load_dialog(user_id: str) -> dict:
    _DIALOG_DIR.mkdir(parents=True, exist_ok=True)
    path = _DIALOG_DIR / f"{user_id}.json"
    if path.exists():
        return json.loads(path.read_text())
    return {"phase": "idle", "answers": [], "qa_round": 0}


def save_dialog(user_id: str, state: dict) -> None:
    _DIALOG_DIR.mkdir(parents=True, exist_ok=True)
    path = _DIALOG_DIR / f"{user_id}.json"
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def reset_dialog(user_id: str) -> None:
    path = _DIALOG_DIR / f"{user_id}.json"
    if path.exists():
        path.unlink()


# ── 执行状态 ────────────────────────────────────────────────────────────────

def _exec_path(plan_path: str) -> Path:
    return Path(plan_path).with_suffix(".state.json")


def load_exec(plan_path: str, all_task_ids: list[int]) -> dict:
    p = _exec_path(plan_path)
    if p.exists():
        return json.loads(p.read_text())
    # 初始化
    return {
        "plan": plan_path,
        "pending": all_task_ids,
        "in_progress": [],
        "completed": [],
        "failed": [],
        "last_wave": 0,
        "tag": "v1.0.0",
    }


def save_exec(plan_path: str, state: dict) -> None:
    _exec_path(plan_path).write_text(
        json.dumps(state, ensure_ascii=False, indent=2)
    )


def cleanup_exec(plan_path: str) -> None:
    p = _exec_path(plan_path)
    if p.exists():
        p.unlink()
