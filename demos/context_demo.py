"""Concept: watch the compaction fold happen on a real run.

A long file-writing task with an absurdly small token budget forces the old half
of the tape to be folded into a summary while the agent keeps working.
Run from the project root:  python -m demos.context_demo [budget_tokens]
Deviation: demos/build_demo.py and the before_tool gate do not exist in this repo
yet, so tools run on the sandbox backend (jailed paths, scrubbed environment)
without a permission gate. Task paths stay inside a fresh temporary folder.
"""
import os
import sys
import tempfile
from pathlib import Path

from zill.context import compact, estimate_tokens
from zill.loop import run_loop
from zill.operations import ops_from_env
from zill.provider import DEFAULT_MODEL
from zill.tools import core_tools

DEFAULT_BUDGET = 1500
NAMES = ["one", "two", "three", "four", "five"]
TASK = ("Create five files one.txt through five.txt, each with 20 lines of the word ping, one write_file at a "
        "time with a read_file check after each; then write MANIFEST.md listing each file and its line count, "
        "verified by counting the lines (wc -l, or a Python one-liner on Windows).")


def main(budget):
    """Run the task with the fold on; print PASS or FAIL and the number of folds."""
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    scratch = tempfile.mkdtemp(prefix="zill-context-")
    os.environ.setdefault("ZILL_BACKEND", "sandbox")
    tools = {t.name: t for t in core_tools(scratch, ops_from_env(scratch))}
    state = {"turn": 0, "folds": 0}

    def on_compact(before, after, head):
        state["folds"] += 1
        print(f"*** compacted: {before} -> {after} tokens ***\n{head[:600]}\n")

    def before_turn(messages):
        """Fold if over budget, and show what is about to be sent."""
        state["turn"] += 1
        folded = compact(DEFAULT_MODEL, messages, budget, on_compact=on_compact)
        print(f"turn {state['turn']}: sending {estimate_tokens(folded)} tokens")
        return folded

    def on_event(message):
        calls = " ".join(c["name"] for c in message.get("tool_calls", []))
        print(f"   [{message['role']}] {(message.get('text') or '')[:80]!r} {calls}")

    system = f"You are a careful coding agent. Work in {scratch}. OS: {sys.platform} (no wc on Windows; use python)."
    messages = [{"role": "user", "text": TASK}]
    run_loop(DEFAULT_MODEL, system, messages, tools, on_event, before_turn=before_turn)

    files_ok = all((Path(scratch) / f"{n}.txt").exists() and
                   len((Path(scratch) / f"{n}.txt").read_text().splitlines()) == 20 for n in NAMES)
    manifest = Path(scratch) / "MANIFEST.md"
    manifest_ok = manifest.exists() and all(f"{n}.txt" in manifest.read_text() for n in NAMES)
    folded_ok = state["folds"] > 0 or budget != DEFAULT_BUDGET  # the default budget must produce a fold
    print(f"\n{'PASS' if files_ok and manifest_ok and folded_ok else 'FAIL'}: "
          f"files_ok={files_ok} manifest_ok={manifest_ok} folds={state['folds']} (budget {budget})")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_BUDGET)
