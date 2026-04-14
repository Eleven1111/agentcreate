"""cmd_interceptor.py — 命令拦截器

职责：
  1. extract_commands(text)  — 从 LLM 输出里提取真实 shell 命令
  2. run_commands(cmds, cwd) — subprocess 执行，返回真实 stdout/stderr
  3. is_circular(a, b)       — 检测两轮输出是否高度重复（车轱辘话）

设计原则：
  - LLM 说"我运行了 X"不算运行，只有这里 subprocess 跑了才算
  - 超时 30s，防止命令挂死整个 wave
  - 不改变调用方行为：无命令时返回空列表，调用方自行降级
"""
import re
import subprocess
from difflib import SequenceMatcher

# 匹配 ```bash / ```sh / ```shell 代码块
_CODE_BLOCK_RE = re.compile(
    r"```(?:bash|sh|shell)\n(.*?)```",
    re.DOTALL | re.IGNORECASE,
)

# 匹配行首 $ 提示符（$ python3 -m agent.agent ...）
_PROMPT_LINE_RE = re.compile(r"^\$\s+(.+)$", re.MULTILINE)

# 高危命令前缀黑名单（保护文件系统）
_DANGEROUS = ("rm ", "rmdir ", "sudo ", "chmod 777", "dd ", "mkfs", "> /dev/")

_TIMEOUT = 30  # seconds


def extract_commands(text: str) -> list[str]:
    """返回 LLM 输出中所有待执行的 shell 命令（去重，保序）。"""
    found: list[str] = []
    seen: set[str] = set()

    for block in _CODE_BLOCK_RE.findall(text):
        for line in block.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                _add(found, seen, line)

    for m in _PROMPT_LINE_RE.finditer(text):
        _add(found, seen, m.group(1).strip())

    return found


def run_commands(
    commands: list[str],
    cwd: str | None = None,
) -> list[dict]:
    """
    顺序执行命令，返回结果列表。
    每项：{"cmd": str, "stdout": str, "stderr": str, "returncode": int}
    """
    results = []
    for cmd in commands:
        if _is_dangerous(cmd):
            results.append({
                "cmd": cmd,
                "stdout": "",
                "stderr": f"[BLOCKED by interceptor: dangerous command pattern]",
                "returncode": -1,
            })
            continue
        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=_TIMEOUT,
                cwd=cwd,
            )
            results.append({
                "cmd": cmd,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "returncode": proc.returncode,
            })
        except subprocess.TimeoutExpired:
            results.append({
                "cmd": cmd,
                "stdout": "",
                "stderr": f"[TIMEOUT after {_TIMEOUT}s]",
                "returncode": -1,
            })
    return results


def is_circular(prev: str, curr: str, threshold: float = 0.72) -> bool:
    """
    判断两轮 LLM 输出是否高度重复。
    threshold=0.72：72% 以上相似就认定为车轱辘话。
    空字符串不计（第一轮没有 prev）。
    """
    if not prev or not curr:
        return False
    ratio = SequenceMatcher(None, prev.strip(), curr.strip()).ratio()
    return ratio >= threshold


# ── 内部工具 ──────────────────────────────────────────────────────────────────

def _add(lst: list[str], seen: set[str], cmd: str) -> None:
    if cmd not in seen:
        seen.add(cmd)
        lst.append(cmd)


def _is_dangerous(cmd: str) -> bool:
    lower = cmd.lower().strip()
    return any(lower.startswith(d) or d in lower for d in _DANGEROUS)
