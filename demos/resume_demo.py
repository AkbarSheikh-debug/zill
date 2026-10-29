"""Concept: durable sessions. Every message is appended to disk as it appears, so a
killed run can be resumed: load() drops a torn last line and repairs open tool calls.
Run from the project root:
  python -m demos.resume_demo start <workdir>     (kill it partway)
  python -m demos.resume_demo resume <workdir>
Wired by hand: harness.py arrives next lecture. Tools run on the sandbox backend.
"""
import os
import sys

from zill import session
from zill.loop import run_loop
from zill.memory import build_system_prompt
from zill.operations import ops_from_env
from zill.provider import DEFAULT_MODEL
from zill.tools import core_tools

TASK = "Create part1.txt through part5.txt one at a time, then SUMMARY.md describing each."
FILES = [f"part{i}.txt" for i in range(1, 6)] + ["SUMMARY.md"]


def main(mode, workdir):
    """Start a persisted run, or resume the latest session; print PASS when all six files exist."""
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    os.makedirs(workdir, exist_ok=True)
    os.environ.setdefault("ZILL_BACKEND", "sandbox")
    tools = {t.name: t for t in core_tools(workdir, ops_from_env(workdir))}
    if mode == "start":
        print(f"pid {os.getpid()} - kill it with Ctrl+C, or `kill -9 {os.getpid()}` (macOS/Linux), "
              f"or `taskkill /F /PID {os.getpid()}` (Windows)", flush=True)
        path = session.new_session(workdir, TASK[:32])
        messages = [{"role": "user", "text": TASK}]
        session.append(path, messages[0])  # the user message is on disk before the loop starts
    else:
        path = session.latest(workdir)
        messages = session.load(path)  # drops a torn tail, repairs open tool calls
        print("last three messages on disk:")
        for m in messages[-3:]:
            print("   ", {k: (str(v)[:100]) for k, v in m.items()})
        messages.append({"role": "user", "text": "continue the task"})
        session.append(path, messages[-1])

    def on_event(message):
        session.append(path, message)  # append is remember
        print(f"   [{message['role']}] {(message.get('text') or '')[:80]!r} "
              + " ".join(c["name"] for c in message.get("tool_calls", [])), flush=True)

    run_loop(DEFAULT_MODEL, build_system_prompt(workdir), messages, tools, on_event)
    print("PASS" if all(os.path.exists(os.path.join(workdir, f)) for f in FILES) else "FAIL")


if __name__ == "__main__":
    main(sys.argv[1], os.path.abspath(sys.argv[2]))
