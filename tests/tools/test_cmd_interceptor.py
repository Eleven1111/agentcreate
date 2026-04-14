"""tests/tools/test_cmd_interceptor.py — 命令拦截器单元测试"""
import pytest
from tools.execute_plan.cmd_interceptor import (
    extract_commands,
    run_commands,
    is_circular,
)


class TestExtractCommands:
    def test_bash_code_block(self):
        text = "先跑这个：\n```bash\npython3 -m agent.agent --keyword AI\n```"
        assert extract_commands(text) == ["python3 -m agent.agent --keyword AI"]

    def test_sh_code_block(self):
        text = "```sh\npytest tests/ -v\n```"
        assert extract_commands(text) == ["pytest tests/ -v"]

    def test_shell_code_block(self):
        text = "```shell\ngit status\n```"
        assert extract_commands(text) == ["git status"]

    def test_dollar_prompt_line(self):
        text = "运行以下命令：\n$ python3 main.py --output result.json"
        assert extract_commands(text) == ["python3 main.py --output result.json"]

    def test_multiple_commands_in_one_block(self):
        text = "```bash\npytest tests/ -v\ngit add .\ngit commit -m 'done'\n```"
        cmds = extract_commands(text)
        assert cmds == ["pytest tests/ -v", "git add .", "git commit -m 'done'"]

    def test_comments_excluded(self):
        text = "```bash\n# 先运行测试\npytest tests/\n```"
        assert extract_commands(text) == ["pytest tests/"]

    def test_deduplication(self):
        text = "```bash\npytest tests/\n```\n$ pytest tests/"
        assert extract_commands(text) == ["pytest tests/"]

    def test_empty_text_returns_empty(self):
        assert extract_commands("这里没有命令，只是文字说明。") == []

    def test_case_insensitive_bash_tag(self):
        text = "```BASH\necho hello\n```"
        assert extract_commands(text) == ["echo hello"]

    def test_multiline_block_preserves_order(self):
        text = "```bash\nstep1\nstep2\nstep3\n```"
        assert extract_commands(text) == ["step1", "step2", "step3"]


class TestRunCommands:
    def test_real_echo_command(self):
        results = run_commands(["echo hello"])
        assert len(results) == 1
        assert results[0]["stdout"].strip() == "hello"
        assert results[0]["returncode"] == 0

    def test_failing_command_captures_returncode(self):
        results = run_commands(["python3 -c 'import sys; sys.exit(42)'"])
        assert results[0]["returncode"] == 42

    def test_stderr_captured(self):
        results = run_commands(["python3 -c 'import sys; sys.stderr.write(\"err\"); sys.exit(1)'"])
        assert "err" in results[0]["stderr"]

    def test_dangerous_rm_blocked(self):
        results = run_commands(["rm -rf /tmp/test_fake_dir"])
        assert results[0]["returncode"] == -1
        assert "BLOCKED" in results[0]["stderr"]

    def test_dangerous_sudo_blocked(self):
        results = run_commands(["sudo cat /etc/passwd"])
        assert results[0]["returncode"] == -1

    def test_multiple_commands_all_run(self):
        results = run_commands(["echo first", "echo second"])
        assert len(results) == 2
        assert results[0]["stdout"].strip() == "first"
        assert results[1]["stdout"].strip() == "second"

    def test_empty_list_returns_empty(self):
        assert run_commands([]) == []


class TestIsCircular:
    def test_identical_text_is_circular(self):
        text = "我正在分析代码结构，发现以下问题：..." * 5
        assert is_circular(text, text) is True

    def test_completely_different_not_circular(self):
        assert is_circular("第一步：写测试", "第二步：实现功能，运行 pytest") is False

    def test_empty_prev_not_circular(self):
        assert is_circular("", "任何内容") is False

    def test_empty_curr_not_circular(self):
        assert is_circular("任何内容", "") is False

    def test_both_empty_not_circular(self):
        assert is_circular("", "") is False

    def test_high_similarity_is_circular(self):
        base = "这是一段很长的输出，描述了当前的代码状态和分析结果。" * 10
        # 只改一个字
        similar = base[:-1] + "。"
        assert is_circular(base, similar) is True

    def test_low_similarity_not_circular(self):
        a = "正在写测试用例，测试 extract_commands 函数的边界情况。"
        b = "实现完成，已运行 pytest，全部通过，准备 commit。"
        assert is_circular(a, b) is False


class TestTaskRunnerIntegration:
    """验证 task_runner 正确接入拦截器。"""

    def _make_task(self, content: str):
        class FakeTask:
            id = 1
            name = "test-task"
        t = FakeTask()
        t.content = content
        return t

    def test_commands_in_response_are_executed_and_injected(self):
        """LLM 输出含命令时，下一轮 user 消息应包含真实执行结果。"""
        from tools.execute_plan.task_runner import run_task

        captured_messages = []
        call_count = [0]

        def llm(messages):
            call_count[0] += 1
            captured_messages.append(messages[:])
            if call_count[0] == 1:
                return "我来运行测试：\n```bash\necho real_output_xyz\n```"
            return "DONE"

        task = self._make_task("写一个 hello world 程序")
        result = run_task(task, "fake_plan.md", "test project", llm)

        assert result["status"] == "DONE"
        # 第二轮的 user 消息应含真实命令输出
        second_round_user = captured_messages[1][-1]["content"]
        assert "real_output_xyz" in second_round_user
        assert "真实 subprocess 输出" in second_round_user

    def test_reanchor_injected_at_round_4(self):
        """第 4 轮 user 消息应含原始任务 spec。"""
        from tools.execute_plan.task_runner import run_task

        captured_messages = []
        call_count = [0]

        def llm(messages):
            call_count[0] += 1
            captured_messages.append(messages[:])
            if call_count[0] >= 5:
                return "DONE"
            return "继续分析中..."

        task = self._make_task("原始任务：实现用户登录功能")
        run_task(task, "fake_plan.md", "test project", llm)

        # 第 5 轮（round_num=4）的 user 消息应有重锚
        round5_user = captured_messages[4][-1]["content"]
        assert "任务重确认" in round5_user
        assert "原始任务：实现用户登录功能" in round5_user

    def test_circular_response_triggers_different_prompt(self):
        """连续两轮高度相似输出时，user 消息应有循环警告。"""
        from tools.execute_plan.task_runner import run_task

        captured_messages = []
        call_count = [0]
        repeated = "我正在分析代码结构，发现以下问题需要处理。" * 20

        def llm(messages):
            call_count[0] += 1
            captured_messages.append(messages[:])
            if call_count[0] >= 3:
                return "DONE"
            return repeated

        task = self._make_task("任务内容")
        run_task(task, "fake_plan.md", "test project", llm)

        # 第三轮 user 消息应有循环警告
        round3_user = captured_messages[2][-1]["content"]
        assert "重复输出" in round3_user
