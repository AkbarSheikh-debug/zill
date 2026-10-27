"""Concept: the harness inside the harness. The parent delegates two jobs through one
tool, spawn_agent; each child runs the same loop on an empty tape and reports back.
Only the parent writes a session log: children do not persist.
Run from the project root:  python -m demos.subagent_demo <workdir>
Wired by hand: harness.py arrives next lecture. Tools run on the sandbox backend.
"""
import os
import subprocess
import sys

from zill import session
from zill.loop import run_loop
from zill.memory import build_system_prompt
from zill.operations import ops_from_env
from zill.provider import DEFAULT_MODEL
from zill.subagent import subagent_tool
from zill.tools import core_tools

TASK = ("Use spawn_agent twice: delegate writing utils.py with a slugify(text) function to one child, and "
        "test_utils.py with five asserts to another; then run python test_utils.py yourself and report.")


class Child:
    """A sub-agent: run(task) is run_loop again with a fresh tape and its own spawn_agent."""

    def __init__(self, workdir, depth):
        self.workdir, self.depth = workdir, depth

    def run(self, task):
        """Run the brief to completion and return the final report."""
        print(f"   {'  ' * self.depth}>> child (depth {self.depth}) starts", flush=True)
        return run_loop(DEFAULT_MODEL, build_system_prompt(self.workdir), [{"role": "user", "text": task}],
                        make_tools(self.workdir, self.depth), lambda m: None)  # no session log for children


def make_tools(workdir, depth):
    """Core tools plus a spawn_agent that makes children one level deeper."""
    tools = core_tools(workdir, ops_from_env(workdir)) + [subagent_tool(lambda d: Child(workdir, d), depth)]
    return {t.name: t for t in tools}


def main(workdir):
    """Run the parent with a persisted session; print PASS when the tests pass and one session file exists."""
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    os.makedirs(workdir, exist_ok=True)
    os.environ.setdefault("ZILL_BACKEND", "sandbox")
    path = session.new_session(workdir, "subagent demo")
    messages = [{"role": "user", "text": TASK}]
    session.append(path, messages[0])

    def on_event(message):
        session.append(path, message)
        print(f"   [{message['role']}] {(message.get('text') or '')[:80]!r} "
              + " ".join(c["name"] for c in message.get("tool_calls", [])), flush=True)

    print(run_loop(DEFAULT_MODEL, build_system_prompt(workdir), messages, make_tools(workdir, 0), on_event))
    tests = subprocess.run([sys.executable, "test_utils.py"], cwd=workdir, capture_output=True, text=True)
    sessions = [n for n in os.listdir(os.path.join(workdir, session.SESSION_DIR)) if n.endswith(".jsonl")]
    print(f"test_utils.py exit {tests.returncode}; session files: {len(sessions)}")
    print("PASS" if tests.returncode == 0 and len(sessions) == 1 else "FAIL")


if __name__ == "__main__":
    main(os.path.abspath(sys.argv[1]))
