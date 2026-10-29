"""Concept: two clocks. Run 1 learns a fact and remember()s it to ZILL.md; run 2 is a
brand-new session whose rebuilt system prompt already contains that fact.
Run from the project root:  python -m demos.memory_demo <workdir>
Wired by hand: harness.py arrives next lecture. Tools run on the sandbox backend.
"""
import os
import sys

from zill.loop import run_loop
from zill.memory import build_system_prompt, remember_tool
from zill.operations import ops_from_env
from zill.provider import DEFAULT_MODEL
from zill.skills import catalog_prompt, use_skill_tool
from zill.tools import core_tools


def make_tools(workdir):
    """Core tools plus remember and use_skill, as a name-keyed dict."""
    os.environ.setdefault("ZILL_BACKEND", "sandbox")
    tools = core_tools(workdir, ops_from_env(workdir)) + [remember_tool(workdir), use_skill_tool(workdir)]
    return {t.name: t for t in tools}


def ask(workdir, task):
    """One whole session: rebuild the prompt from disk, run the loop, return (answer, messages)."""
    system = build_system_prompt(workdir, catalog_prompt(workdir))  # the prompt is rebuilt per session
    messages = [{"role": "user", "text": task}]
    shown = lambda m: print(f"   [{m['role']}] {(m.get('text') or '')[:90]!r} "
                            + " ".join(c["name"] for c in m.get("tool_calls", [])))
    return run_loop(DEFAULT_MODEL, system, messages, make_tools(workdir), shown), messages


def main(workdir):
    """Run both sessions and print PASS when run 2 knows 'make test'."""
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    os.makedirs(workdir, exist_ok=True)
    with open(os.path.join(workdir, "Makefile"), "w", encoding="utf-8", newline="\n") as f:
        f.write("test:\n\tpython -m unittest\n")
    print("--- run 1 ---")
    answer1, _ = ask(workdir, "Find out how this project runs its tests, then remember it.")
    print("--- run 2 (fresh messages, rebuilt prompt) ---")
    answer2, messages2 = ask(workdir, "How do I run the tests here? Answer in one line without using any tools.")
    tool_calls = sum(len(m.get("tool_calls", [])) for m in messages2)
    print(f"\nrun 1 answer: {answer1}\nrun 2 answer: {answer2}\nrun 2 tool calls: {tool_calls}")
    print("ZILL.md:", open(os.path.join(workdir, "ZILL.md"), encoding="utf-8").read().strip())
    print("PASS" if "make test" in answer2.lower() else "FAIL")


if __name__ == "__main__":
    main(os.path.abspath(sys.argv[1]))
